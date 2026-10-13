from decimal import Decimal

import pandas as pd
import pytest
from gosdoc.tables.spec import MappingSpec
from gosdoc.tables.transforms import SpecError, apply_mapping, parse_money
from pydantic import ValidationError


def frame(rows: list[dict[str, str]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, dtype=str)
    df.index = df.index + 2
    return df


def run(
    columns: list[dict[str, object]], rows: list[dict[str, str]], **extra: object
) -> pd.DataFrame:
    result = apply_mapping(MappingSpec.model_validate({"columns": columns, **extra}), frame(rows))
    return result.data


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12 500,00", "12500.00"),
        ("8 300,50 руб.", "8300.50"),
        ("1.234,5", "1234.50"),
        ("1,234.5", "1234.50"),
        ("-12", "-12.00"),
        ("15000", "15000.00"),
    ],
)
def test_parse_money(raw: str, expected: str) -> None:
    assert parse_money(raw) == Decimal(expected)


def test_operations() -> None:
    data = run(
        [
            {"target": "ФИО", "op": "fio_join", "sources": ["Ф", "И", "О"], "style": "initials"},
            {"target": "Фамилия", "op": "fio_split", "source": "Полное", "part": "last"},
            {"target": "Отчество", "op": "fio_split", "source": "Полное", "part": "middle"},
            {"target": "Адрес", "op": "concat", "sources": ["Город", "Улица"], "sep": ", "},
            {"target": "Дом", "op": "split", "source": "Улица", "sep": " д. ", "index": 1},
            {"target": "Дата", "op": "date", "source": "Д", "out_format": "%Y-%m-%d"},
            {
                "target": "Тип",
                "op": "dict",
                "source": "Т",
                "map": {"a": "Альфа"},
                "default": "Прочее",
            },
            {"target": "Источник", "op": "const", "value": "Реестр 2026"},
        ],
        [
            {
                "Ф": "Иванов",
                "И": "иван",
                "О": "Иванович",
                "Полное": "Петров Пётр Петрович",
                "Город": "Москва",
                "Улица": "ул. Ленина д. 5",
                "Д": "2024-01-15 00:00:00",
                "Т": "b",
            }
        ],
    )
    assert data.iloc[0].to_dict() == {
        "ФИО": "Иванов И.И.",
        "Фамилия": "Петров",
        "Отчество": "Петрович",
        "Адрес": "Москва, ул. Ленина д. 5",
        "Дом": "5",
        "Дата": "2024-01-15",
        "Тип": "Прочее",
        "Источник": "Реестр 2026",
    }


def test_errors_go_to_report_not_lost() -> None:
    spec = MappingSpec.model_validate(
        {
            "columns": [
                {"target": "Дата", "op": "date", "source": "Д"},
                {"target": "Пол", "op": "dict", "source": "П", "map": {"м": "М"}},
                {"target": "ИНН", "source": "ИНН"},
            ],
            "filter": [{"column": "С", "op": "ne", "value": "архив"}],
            "validate": {"ИНН": ["required", "inn"]},
        }
    )
    rows = [
        {"Д": "01.02.2020", "П": "м", "ИНН": "7707083893", "С": ""},
        {"Д": "мусор", "П": "x", "ИНН": "7707083893", "С": ""},
        {"Д": "01.02.2020", "П": "м", "ИНН": "", "С": ""},
        {"Д": "01.02.2020", "П": "м", "ИНН": "7707083893", "С": "Архив"},
    ]
    result = apply_mapping(spec, frame(rows))
    assert result.summary() == {
        "rows_in": 4,
        "rows_out": 1,
        "rows_filtered": 1,
        "rows_rejected": 2,
        "rows_written": 1,
    }
    bad = next(i for i in result.issues if i.row == 3)
    assert "Дата: не удалось распознать дату" in bad.reason and "нет в справочнике" in bad.reason
    assert bad.values["Д"] == "мусор"  # исходная строка целиком в отчёте
    assert next(i for i in result.issues if i.row == 5).status == "отфильтрована"


def test_group_by_sums_and_counts() -> None:
    data = run(
        [
            {"target": "Отдел", "source": "О"},
            {"target": "Сумма", "op": "money", "source": "С"},
            {"target": "Человек", "op": "const", "value": "1"},
        ],
        [{"О": "А", "С": "100,50"}, {"О": "Б", "С": "10"}, {"О": "А", "С": "1 000"}],
        group_by={"keys": ["Отдел"], "agg": {"Сумма": "sum", "Человек": "count"}},
    )
    assert data.to_dict("records") == [
        {"Отдел": "А", "Сумма": "1100.50", "Человек": "2"},
        {"Отдел": "Б", "Сумма": "10.00", "Человек": "1"},
    ]


def test_spec_errors() -> None:
    with pytest.raises(ValidationError, match="нужна колонка source"):
        MappingSpec.model_validate({"columns": [{"target": "A", "op": "copy"}]})
    with pytest.raises(ValidationError, match="Нет таких колонок шаблона"):
        MappingSpec.model_validate(
            {"columns": [{"target": "A", "source": "a"}], "validate": {"B": ["inn"]}}
        )
    spec = MappingSpec.model_validate({"columns": [{"target": "A", "source": "нет"}]})
    with pytest.raises(SpecError, match="нет колонок: нет"):
        apply_mapping(spec, frame([{"a": "1"}]))
