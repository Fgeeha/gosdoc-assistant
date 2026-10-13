"""Декларативная спецификация маппинга (docs/architecture.md, п. 4.3). Кода в ней нет."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Op = Literal["copy", "concat", "split", "date", "fio_split", "fio_join", "money", "dict", "const"]
Check = Literal["required", "inn", "snils", "ogrn", "int", "number", "date"]
_NEEDS_SOURCE = {"copy", "split", "date", "fio_split", "money", "dict"}
_NEEDS_SOURCES = {"concat", "fio_join"}


class ColumnRule(BaseModel):
    """Как получить значение колонки шаблона из строки источника."""

    target: str = Field(min_length=1)
    op: Op = "copy"
    source: str | None = None  # одна колонка источника
    sources: list[str] = []  # несколько колонок (concat, fio_join)
    sep: str = " "  # concat, split
    index: int = Field(default=0, ge=0)  # split: номер части с нуля
    in_format: str = "auto"  # date: формат strptime или auto
    out_format: str = "%d.%m.%Y"  # date
    part: Literal["last", "first", "middle"] = "last"  # fio_split: фамилия / имя / отчество
    style: Literal["full", "initials"] = "full"  # fio_join: полностью / «Иванов И.И.»
    map: dict[str, str] = {}  # dict: значение источника → значение шаблона
    default: str | None = None  # dict: None — значение не из справочника считается ошибкой
    value: str = ""  # const

    @model_validator(mode="after")
    def _check_op(self) -> Self:
        if self.op in _NEEDS_SOURCE and not self.source:
            raise ValueError(f"«{self.target}»: для операции {self.op} нужна колонка source")
        if self.op in _NEEDS_SOURCES and not self.sources:
            raise ValueError(f"«{self.target}»: для операции {self.op} нужен список sources")
        if self.op == "dict" and not self.map:
            raise ValueError(f"«{self.target}»: для операции dict нужен справочник map")
        return self

    def source_columns(self) -> list[str]:
        return [*([self.source] if self.source else []), *self.sources]


class FilterRule(BaseModel):
    """Строка источника переносится, только если проходит все фильтры."""

    column: str
    op: Literal["eq", "ne", "in", "not_in", "contains", "empty", "not_empty"]
    value: str | list[str] = ""


class GroupBy(BaseModel):
    keys: list[str] = Field(min_length=1)  # колонки шаблона
    agg: dict[str, Literal["sum", "count", "first"]] = {}  # прочие колонки — first


class MappingSpec(BaseModel):
    # Ключ JSON — "validate", как в архитектуре; поле названо иначе, чтобы не
    # перекрывать метод BaseModel.
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    columns: list[ColumnRule] = Field(min_length=1)
    filter: list[FilterRule] = []
    group_by: GroupBy | None = None
    checks: dict[str, list[Check]] = Field(default_factory=dict, alias="validate")

    @model_validator(mode="after")
    def _check_targets(self) -> Self:
        targets = [c.target for c in self.columns]
        duplicates = {t for t in targets if targets.count(t) > 1}
        if duplicates:
            raise ValueError(f"Колонки шаблона заданы дважды: {', '.join(sorted(duplicates))}")
        referenced = set(self.checks) | set(self.group_by.keys if self.group_by else [])
        unknown = referenced - set(targets)
        if unknown:
            raise ValueError(f"Нет таких колонок шаблона: {', '.join(sorted(unknown))}")
        return self

    def source_columns(self) -> set[str]:
        from_rules = {c for rule in self.columns for c in rule.source_columns()}
        return from_rules | {f.column for f in self.filter}
