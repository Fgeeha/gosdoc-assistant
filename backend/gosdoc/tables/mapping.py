"""Предложение маппинга: эвристики по названиям и данным, для неуверенных колонок — модель.

В модель уходят только названия колонок и «форма» до трёх значений: буквы
заменены на А/а, цифры на 9 («Иванов И.И.» → «Аааааа А.А.»). Данные строк
модель не видит и не преобразует.
"""

import difflib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
import pandas as pd

from gosdoc.core.llm import LLMClient
from gosdoc.tables.spec import ColumnRule, MappingSpec
from gosdoc.tables.transforms import TransformError, parse_date, parse_money

log = logging.getLogger(__name__)

PROMPT_VERSION = "tables-mapping-v1"
CONFIDENT = 0.75  # ниже — спрашиваем модель
Origin = Literal["heuristic", "llm", "none"]

# Группы синонимов по нормализованным названиям колонок.
_SYNONYMS = [
    {"фио", "фамилияимяотчество", "сотрудник", "работник", "получатель", "заявитель"},
    {"фамилия"},
    {"имя"},
    {"отчество"},
    {"датарождения", "др", "датарожд", "рождение"},
    {"инн", "иннфизлица", "иннорганизации"},
    {"снилс", "страховойномер", "номерснилс"},
    {"огрн", "огрнип"},
    {"сумма", "суммаруб", "суммарублей", "размервыплаты", "выплата", "начислено"},
    {"пол"},
    {"телефон", "тел", "номертелефона", "контактныйтелефон"},
    {"email", "электроннаяпочта", "почта", "адресэлектроннойпочты"},
    {"адрес", "адресрегистрации", "адреспроживания"},
    {"должность"},
    {"подразделение", "отдел", "структурноеподразделение"},
]
_FIO_PARTS = {"фамилия": "last", "имя": "first", "отчество": "middle"}

_PROMPT = """Сопоставь колонки целевого шаблона с колонками исходной таблицы.
Колонки источника и форма их значений (буквы заменены на А/а, цифры на 9):
{sources}
Колонки шаблона, для которых нужно найти источник: {targets}
Ответь только JSON-объектом вида {{"колонка шаблона": "колонка источника или null"}}.
Используй только перечисленные названия колонок источника."""


@dataclass
class Suggestion:
    spec: MappingSpec
    confidence: dict[str, float] = field(default_factory=dict)
    origin: dict[str, Origin] = field(default_factory=dict)
    prompt_version: str = PROMPT_VERSION


def normalize(name: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", name.lower().replace("ё", "е"))


def value_shape(value: str) -> str:
    """Форма значения без самих данных."""
    out = re.sub(r"[A-ZА-ЯЁ]", "А", value)
    out = re.sub(r"[a-zа-яё]", "а", out)
    return re.sub(r"\d", "9", out)[:40]


def _group(name: str) -> int | None:
    n = normalize(name)
    return next((i for i, g in enumerate(_SYNONYMS) if n in g), None)


def _name_score(target: str, source: str) -> float:
    t, s = normalize(target), normalize(source)
    if not t or not s:
        return 0.0
    if t == s:
        return 1.0
    group = _group(target)
    if group is not None and group == _group(source):
        return 0.9
    return difflib.SequenceMatcher(None, t, s).ratio() * 0.85


def _parses(values: list[str], parser: Callable[[str], object]) -> bool:
    """Большинство примеров (80 %) разбирается: единичные ошибки уйдут в отчёт."""
    if not values:
        return False
    ok = 0
    for v in values:
        try:
            parser(v)
            ok += 1
        except TransformError:
            continue
    return ok / len(values) >= 0.8


def _rule_for(target: str, source: str, samples: list[str]) -> ColumnRule:
    """Операция по смыслу колонки шаблона и виду данных источника."""
    t = normalize(target)
    if "дата" in t and _parses(samples, parse_date):
        return ColumnRule(target=target, op="date", source=source)
    if _group(target) == _group("сумма") and _parses(samples, parse_money):
        return ColumnRule(target=target, op="money", source=source)
    return ColumnRule(target=target, op="copy", source=source)


def _fio_rule(target: str, source_cols: list[str]) -> ColumnRule | None:
    """ФИО целиком из трёх колонок или часть ФИО из одной."""
    by_norm = {normalize(c): c for c in source_cols}
    if _group(target) == _group("фио") and all(p in by_norm for p in ("фамилия", "имя")):
        parts = [by_norm[p] for p in ("фамилия", "имя", "отчество") if p in by_norm]
        return ColumnRule(target=target, op="fio_join", sources=parts)
    part = _FIO_PARTS.get(normalize(target))
    fio = next((c for c in source_cols if _group(c) == _group("фио")), None)
    if part and fio and normalize(target) not in by_norm:
        return ColumnRule(target=target, op="fio_split", source=fio, part=part)  # type: ignore[arg-type]
    return None


def suggest_mapping(source: pd.DataFrame, targets: list[str], llm: LLMClient | None) -> Suggestion:
    source_cols = list(source.columns)
    samples = {c: [v for v in source[c].tolist() if str(v).strip()][:5] for c in source_cols}
    rules: dict[str, ColumnRule] = {}
    result = Suggestion(spec=MappingSpec(columns=[ColumnRule(target="_", op="const")]))

    for target in targets:
        fio = _fio_rule(target, source_cols)
        if fio:
            rules[target], result.confidence[target] = fio, 0.9
            result.origin[target] = "heuristic"
            continue
        best = max(source_cols, key=lambda s: _name_score(target, s), default=None)
        score = _name_score(target, best) if best else 0.0
        if best and score >= CONFIDENT:
            rules[target] = _rule_for(target, best, samples[best])
            result.confidence[target], result.origin[target] = round(score, 2), "heuristic"

    unsure = [t for t in targets if t not in rules]
    if unsure and llm is not None:
        for target, src in _ask_model(llm, unsure, source_cols, samples).items():
            rules[target] = _rule_for(target, src, samples[src])
            result.confidence[target], result.origin[target] = 0.6, "llm"

    for target in targets:
        if target not in rules:  # пользователь выберет источник сам
            rules[target] = ColumnRule(target=target, op="const", value="")
            result.confidence[target], result.origin[target] = 0.0, "none"
    result.spec = MappingSpec(columns=[rules[t] for t in targets])
    return result


def _ask_model(
    llm: LLMClient, targets: list[str], source_cols: list[str], samples: dict[str, list[str]]
) -> dict[str, str]:
    described = "\n".join(
        f"- {c}: {', '.join(value_shape(str(v)) for v in samples[c][:3]) or '—'}"
        for c in source_cols
    )
    prompt = _PROMPT.format(sources=described, targets=", ".join(targets))
    try:
        answer = llm.chat([{"role": "user", "content": prompt}])
        raw = json.loads(answer[answer.find("{") : answer.rfind("}") + 1] or "{}")
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("Модель не предложила маппинг: %s", type(e).__name__)
        return {}
    if not isinstance(raw, dict):
        return {}
    # Только существующие колонки: выдуманные моделью названия отбрасываются.
    return {t: s for t, s in raw.items() if t in targets and s in source_cols}
