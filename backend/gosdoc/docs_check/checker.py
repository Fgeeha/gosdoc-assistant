"""Проверка документа: ссылки на НПА по базе и содержание по найденным нормам.

Принцип «нет источника — нет утверждения»: замечание по содержанию попадает в
отчёт, только если цитата модели дословно найдена в тексте фрагмента нормы.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from gosdoc.docs_check.models import NpaAct, NpaChunk, NpaEdition
from gosdoc.docs_check.npa import SearchBackends, act_label, edition_at, find_act
from gosdoc.docs_check.npa_refs import NpaRef, extract_refs
from rag.search import rrf

log = logging.getLogger(__name__)
MIN_PARAGRAPH_CHARS = 80  # короче — обычно реквизиты и заголовки
MAX_CONTENT_PARAGRAPHS = 30
TOP_K = 5
CHARS_PER_TOKEN = 3  # грубая оценка для русского текста


@dataclass
class FindingDraft:
    kind: str  # npa_ref / content
    severity: str  # critical / warning / info
    paragraph: int
    doc_fragment: str
    message: str
    npa_ref: str = ""
    chunk_id: int | None = None
    norm: str = ""
    quote: str = ""
    recommendation: str = ""


@dataclass
class CheckResult:
    findings: list[FindingDraft] = field(default_factory=list)
    refs_total: int = 0
    paragraphs_checked: int = 0
    unverified_dropped: int = 0  # ответы модели без подтверждённой цитаты
    meta: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        by = {
            s: sum(f.severity == s for f in self.findings) for s in ("critical", "warning", "info")
        }
        return {
            "refs_total": self.refs_total,
            "paragraphs_checked": self.paragraphs_checked,
            "unverified_dropped": self.unverified_dropped,
            "findings": len(self.findings),
            **by,
            **self.meta,
        }


def _norm(text: str) -> str:
    text = re.sub(r"[«»\"„“”]", "", text).replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in(quote: str, source: str) -> bool:
    q = _norm(quote).strip(' ."')
    return len(q) >= 15 and q in _norm(source)


def _chunk_at(db: Session, edition: NpaEdition, path: str) -> NpaChunk | None:
    """Фрагмент по пути «ст. 9 ч. 1»; для «ст. 9» — первый фрагмент статьи."""
    rows = list(
        db.scalars(select(NpaChunk).where(NpaChunk.edition_id == edition.id).order_by(NpaChunk.id))
    )
    exact = [r for r in rows if r.path == path or r.path.startswith(path + " ")]
    return exact[0] if exact else None


def _fragment(paragraphs: list[str], ref: NpaRef) -> str:
    return paragraphs[ref.paragraph][:500]


# --- Ссылки на НПА ---------------------------------------------------------------


def check_ref(
    db: Session, organization_id: int, ref: NpaRef, paragraphs: list[str], today: date
) -> list[FindingDraft]:
    base: dict[str, Any] = {
        "kind": "npa_ref",
        "paragraph": ref.paragraph,
        "doc_fragment": _fragment(paragraphs, ref),
        "npa_ref": ref.text,
    }
    act = find_act(db, organization_id, ref.key)
    if act is None:
        return [
            FindingDraft(
                severity="info",
                message="Акт не найден в нормативной базе — проверьте ссылку вручную",
                **base,
            )
        ]
    label = act_label(act)
    found: list[FindingDraft] = []
    if act.repealed_at and act.repealed_at <= today:
        old = edition_at(db, act.id, act.repealed_at)
        chunk = _chunk_at(db, old, ref.path) if old and ref.path else None
        found.append(
            FindingDraft(
                severity="critical",
                message=f"Ссылка на акт, утративший силу с {act.repealed_at:%d.%m.%Y}. {act.repealed_note}".strip(),
                norm=label + (f", {ref.path}" if ref.path else ""),
                quote=act.repealed_note or (chunk.text if chunk else ""),
                chunk_id=chunk.id if chunk else None,
                recommendation="Замените ссылку на действующий акт, регулирующий этот вопрос",
                **base,
            )
        )
        return found
    if ref.adopted_at and act.adopted_at and ref.adopted_at != act.adopted_at:
        found.append(
            FindingDraft(
                severity="warning",
                message=f"Дата акта указана неверно: в базе {act.adopted_at:%d.%m.%Y}",
                norm=label,
                recommendation=f"Исправьте дату на {act.adopted_at:%d.%m.%Y}",
                **base,
            )
        )
    current = edition_at(db, act.id, today)
    if current is None:
        return found
    if ref.edition_date and current.effective_from > ref.edition_date:
        cited = edition_at(db, act.id, ref.edition_date)
        new_chunk = _chunk_at(db, current, ref.path) if ref.path else None
        old_chunk = _chunk_at(db, cited, ref.path) if cited and ref.path else None
        changed = (
            new_chunk is None or old_chunk is None or _norm(new_chunk.text) != _norm(old_chunk.text)
        )
        if changed or not ref.path:
            message = (
                f"Ссылка на устаревшую редакцию от {ref.edition_date:%d.%m.%Y}; "
                f"действующая редакция — с {current.effective_from:%d.%m.%Y}"
            )
            if ref.path and old_chunk and new_chunk:
                message += f". Текст {ref.path} изменился"
            found.append(
                FindingDraft(
                    severity="warning",
                    message=message,
                    norm=label + (f", {ref.path}" if ref.path else ""),
                    quote=new_chunk.text if new_chunk else "",
                    chunk_id=new_chunk.id if new_chunk else None,
                    recommendation="Укажите действующую редакцию и сверьте текст документа с нормой",
                    **base,
                )
            )
    elif ref.path and _chunk_at(db, current, ref.path) is None:
        found.append(
            FindingDraft(
                severity="warning",
                message=f"В действующей редакции нет {ref.path}",
                norm=label,
                recommendation="Проверьте номер статьи и части",
                **base,
            )
        )
    return found


# --- Содержание (RAG) -------------------------------------------------------------


@cache
def _prompt() -> dict[str, str]:
    data: dict[str, str] = yaml.safe_load(
        (Path(__file__).parent / "prompts.yaml").read_text("utf-8")
    )
    return data


def retrieve(
    backends: SearchBackends, organization_id: int, query: str, k: int = TOP_K
) -> list[int]:
    text_ids = backends.text.search(query, organization_id, k * 2)
    vector = backends.llm.embed([query])[0]
    vector_ids = backends.vectors.search(vector, organization_id, k * 2)
    return [int(i) for i in rrf([text_ids, vector_ids])[:k]]


def _current_chunks(db: Session, ids: list[int], today: date) -> list[tuple[NpaChunk, NpaAct]]:
    """Только фрагменты редакций, действующих сегодня, и не отменённых актов."""
    out = []
    for chunk_id in ids:
        chunk = db.get(NpaChunk, chunk_id)
        edition = db.get(NpaEdition, chunk.edition_id) if chunk else None
        act = db.get(NpaAct, edition.act_id) if edition else None
        if chunk is None or edition is None or act is None:
            continue
        if act.repealed_at and act.repealed_at <= today:
            continue
        if edition.effective_from > today or (
            edition.effective_to and edition.effective_to < today
        ):
            continue
        out.append((chunk, act))
    return out


def check_content(
    db: Session,
    backends: SearchBackends,
    organization_id: int,
    paragraphs: list[str],
    today: date,
    max_context: int,
    result: CheckResult,
) -> None:
    prompt = _prompt()
    budget = max_context * CHARS_PER_TOKEN // 2  # половина контекста — под нормы
    candidates = [(i, p) for i, p in enumerate(paragraphs) if len(p) >= MIN_PARAGRAPH_CHARS][
        :MAX_CONTENT_PARAGRAPHS
    ]
    for i, paragraph in candidates:
        norms = _current_chunks(db, retrieve(backends, organization_id, paragraph), today)
        selected: list[tuple[NpaChunk, NpaAct]] = []
        used = 0
        for chunk, act in norms:
            if used + len(chunk.text) > budget and selected:
                break
            selected.append((chunk, act))
            used += len(chunk.text)
        if not selected:
            continue
        result.paragraphs_checked += 1
        listing = "\n".join(
            f"[{n}] {act_label(a)}, {c.path}: {c.text}" for n, (c, a) in enumerate(selected, 1)
        )
        user = prompt["user"].format(fragment=paragraph[:2000], norms=listing)
        try:
            answer = backends.llm.chat(
                [{"role": "system", "content": prompt["system"]}, {"role": "user", "content": user}]
            )
            raw = json.loads(answer[answer.find("{") : answer.rfind("}") + 1] or "{}")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("Модель не ответила по абзацу %d: %s", i, type(e).__name__)
            continue
        if not isinstance(raw, dict) or raw.get("verdict") != "violation":
            continue
        try:
            chunk, act = selected[int(raw.get("source", 0)) - 1]
        except (ValueError, IndexError, TypeError):
            result.unverified_dropped += 1
            continue
        quote = str(raw.get("quote", ""))
        if not quote_in(quote, chunk.text):
            result.unverified_dropped += 1  # нет подтверждённого источника — не утверждаем
            continue
        result.findings.append(
            FindingDraft(
                kind="content",
                severity="warning",
                paragraph=i,
                doc_fragment=paragraph[:500],
                message=str(raw.get("explanation", "")).strip() or "Возможное несоответствие норме",
                chunk_id=chunk.id,
                norm=f"{act_label(act)}, {chunk.path}",
                quote=quote.strip(),
                recommendation=str(raw.get("recommendation", "")).strip(),
            )
        )
    result.meta["prompt_version"] = prompt["version"]
    result.meta["model_name"] = backends.llm.s.llm_model


def check_document(
    db: Session,
    backends: SearchBackends | None,
    organization_id: int,
    paragraphs: list[str],
    today: date,
    max_context: int,
) -> CheckResult:
    result = CheckResult()
    refs = extract_refs(paragraphs)
    result.refs_total = len(refs)
    for ref in refs:
        result.findings += check_ref(db, organization_id, ref, paragraphs, today)
    if backends is not None:
        check_content(db, backends, organization_id, paragraphs, today, max_context, result)
    return result
