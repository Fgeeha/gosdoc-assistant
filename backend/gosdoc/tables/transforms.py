"""Применение маппинга: детерминированные преобразования на pandas, без модели.

Каждая строка источника либо попадает в результат, либо в отчёт с причиной
(отфильтрована / отклонена). Инвариант rows_in = rows_out + rows_filtered +
rows_rejected проверяется в конце.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from gosdoc.tables.spec import ColumnRule, FilterRule, GroupBy, MappingSpec
from gosdoc.tables.validators import validate_value

# Номер строки в файле: 1 — заголовок, данные с 2.
FIRST_DATA_ROW = 2
_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y %H:%M:%S",
)


class TransformError(ValueError):
    """Значение нельзя преобразовать; текст попадает в отчёт об ошибках."""


class SpecError(ValueError):
    """Маппинг не подходит к файлу (нет колонок и т. п.)."""


@dataclass
class RowIssue:
    row: int  # номер строки в файле источника
    status: str  # «отфильтрована» / «отклонена»
    reason: str
    values: dict[str, str]  # исходная строка целиком — ничего не теряется


@dataclass
class TransferResult:
    data: pd.DataFrame  # колонки шаблона
    rows_in: int
    rows_out: int  # строк источника, попавших в результат (до группировки)
    issues: list[RowIssue] = field(default_factory=list)
    numeric_columns: set[str] = field(default_factory=set)

    @property
    def rows_filtered(self) -> int:
        return sum(i.status == "отфильтрована" for i in self.issues)

    @property
    def rows_rejected(self) -> int:
        return sum(i.status == "отклонена" for i in self.issues)

    def summary(self) -> dict[str, int]:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_filtered": self.rows_filtered,
            "rows_rejected": self.rows_rejected,
            "rows_written": len(self.data),
        }


# --- Операции ----------------------------------------------------------------


def parse_date(value: str, in_format: str = "auto") -> datetime:
    formats = _DATE_FORMATS if in_format == "auto" else (in_format,)
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise TransformError(f"не удалось распознать дату «{value}»")


def parse_money(value: str) -> Decimal:
    """«1 234,50 руб.», «1234.5», «-12,00» → Decimal."""
    cleaned = re.sub(r"[^\d,.\-]", "", value.replace("\xa0", "")).strip(".,")  # «руб.»
    if "," in cleaned and "." in cleaned:  # «1.234,50» или «1,234.50»
        thousands = "." if cleaned.rfind(",") > cleaned.rfind(".") else ","
        cleaned = cleaned.replace(thousands, "")
    try:
        return Decimal(cleaned.replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise TransformError(f"не удалось распознать сумму «{value}»") from None


def _fio_split(value: str, part: str) -> str:
    words = value.split()
    if not words:
        return ""
    if len(words) < 2 and part != "last":
        raise TransformError(f"в ФИО «{value}» нет имени")
    return {"last": words[0], "first": words[1], "middle": " ".join(words[2:])}[part]


def _fio_join(parts: list[str], style: str) -> str:
    parts = [p for p in parts if p]
    if style == "full" or not parts:
        return " ".join(parts)
    return parts[0] + " " + "".join(f"{p[0].upper()}." for p in parts[1:])


def _dict(value: str, rule: ColumnRule) -> str:
    if not value:
        return ""
    lookup = {k.strip().lower(): v for k, v in rule.map.items()}
    found = lookup.get(value.strip().lower(), rule.default)
    if found is None:
        raise TransformError(f"значения «{value}» нет в справочнике")
    return found


def _date(value: str, rule: ColumnRule) -> str:
    return parse_date(value, rule.in_format).strftime(rule.out_format) if value else ""


def _money(value: str) -> str:
    return str(parse_money(value)) if value.strip() else ""


def _split(value: str, rule: ColumnRule) -> str:
    parts = value.split(rule.sep)
    return parts[rule.index].strip() if rule.index < len(parts) else ""


Row = dict[str, str]
_OPS: dict[str, Callable[[Row, ColumnRule], str]] = {
    "copy": lambda r, c: r[c.source or ""],
    "concat": lambda r, c: c.sep.join(r[s] for s in c.sources if r[s]),
    "split": lambda r, c: _split(r[c.source or ""], c),
    "date": lambda r, c: _date(r[c.source or ""], c),
    "fio_split": lambda r, c: _fio_split(r[c.source or ""], c.part),
    "fio_join": lambda r, c: _fio_join([r[s] for s in c.sources], c.style),
    "money": lambda r, c: _money(r[c.source or ""]),
    "dict": lambda r, c: _dict(r[c.source or ""], c),
    "const": lambda r, c: c.value,
}


def _passes(row: Row, f: FilterRule) -> bool:
    value = row[f.column].strip()
    expected = [v.strip().lower() for v in ([f.value] if isinstance(f.value, str) else f.value)]
    match f.op:
        case "eq":
            return value.lower() == expected[0]
        case "ne":
            return value.lower() != expected[0]
        case "in":
            return value.lower() in expected
        case "not_in":
            return value.lower() not in expected
        case "contains":
            return expected[0] in value.lower()
        case "empty":
            return not value
        case _:  # not_empty
            return bool(value)


def _describe(f: FilterRule) -> str:
    return f"{f.column} {f.op} {f.value}".strip()


# --- Применение ----------------------------------------------------------------


def check_columns(spec: MappingSpec, columns: list[str]) -> None:
    missing = spec.source_columns() - set(columns)
    if missing:
        raise SpecError(f"В файле нет колонок: {', '.join(sorted(missing))}")


def apply_mapping(spec: MappingSpec, source: pd.DataFrame) -> TransferResult:
    """source — все значения строками, индекс — номер строки в файле."""
    check_columns(spec, list(source.columns))
    targets = [c.target for c in spec.columns]
    accepted: list[Row] = []
    issues: list[RowIssue] = []

    for row_no, series in source.iterrows():
        row: Row = {str(k): str(v) for k, v in series.items()}
        failed = next((f for f in spec.filter if not _passes(row, f)), None)
        if failed is not None:
            issues.append(RowIssue(int(row_no), "отфильтрована", _describe(failed), row))  # type: ignore[call-overload]
            continue
        out: Row = {}
        errors: list[str] = []
        for rule in spec.columns:
            try:
                out[rule.target] = _OPS[rule.op](row, rule)
            except TransformError as e:
                out[rule.target] = ""
                errors.append(f"{rule.target}: {e}")
        for target, checks in spec.checks.items():
            errors += [f"{target}: {m}" for m in validate_value(out[target], list(checks))]
        if errors:
            issues.append(RowIssue(int(row_no), "отклонена", "; ".join(errors), row))  # type: ignore[call-overload]
        else:
            accepted.append(out)

    data = pd.DataFrame(accepted, columns=targets, dtype=object)
    numeric = {c.target for c in spec.columns if c.op == "money"}
    if spec.group_by:
        data = _group(data, spec.group_by, numeric)
    result = TransferResult(data, len(source), len(accepted), issues, numeric)
    if result.rows_in != result.rows_out + result.rows_filtered + result.rows_rejected:
        raise AssertionError("Нарушен инвариант переноса: строки потеряны")
    return result


def _group(data: pd.DataFrame, group_by: GroupBy, numeric: set[str]) -> pd.DataFrame:
    keys = group_by.keys
    rows = []
    for _, group in data.groupby(keys, sort=False, dropna=False):
        out = group.iloc[0].to_dict()
        for column, how in group_by.agg.items():
            if how == "count":
                out[column] = str(len(group))
            elif how == "sum":
                total = sum((parse_money(v) for v in group[column] if str(v).strip()), Decimal(0))
                out[column] = str(total.quantize(Decimal("0.01")))
                numeric.add(column)
        rows.append(out)
    return pd.DataFrame(rows, columns=list(data.columns), dtype=object)
