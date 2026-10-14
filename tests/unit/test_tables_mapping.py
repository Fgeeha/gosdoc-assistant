import json

import httpx
import pandas as pd
import pytest
from gosdoc.core.config import Settings
from gosdoc.core.llm import LLMClient
from gosdoc.tables.mapping import suggest_mapping, value_shape
from gosdoc.tables.tableio import read_columns, read_table

from tests.tables_spec import FIXTURES


def fake_llm(answer: str, prompts: list[str]) -> LLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        prompts.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})

    return LLMClient(Settings(), transport=httpx.MockTransport(handler))


@pytest.fixture
def source() -> pd.DataFrame:
    df = read_table((FIXTURES / "source.xlsx").read_bytes(), "source.xlsx")
    df["Таб. №"] = "000123"
    return df


def test_value_shape_hides_data() -> None:
    assert value_shape("Иванов И.И.") == "Аааааа А.А."
    assert value_shape("112-233-445 95") == "999-999-999 99"


def test_heuristics_cover_fixture_template(source: pd.DataFrame) -> None:
    targets = read_columns((FIXTURES / "template.xlsx").read_bytes(), "template.xlsx")
    s = suggest_mapping(source, targets, llm=None)
    rules = {r.target: r for r in s.spec.columns}
    assert rules["ФИО"].op == "fio_join" and rules["ФИО"].sources == ["Фамилия", "Имя", "Отчество"]
    assert (rules["Дата рождения"].op, rules["Дата рождения"].source) == ("date", "ДР")
    assert (rules["Сумма"].op, rules["Сумма"].source) == ("money", "Сумма, руб.")
    assert rules["Подразделение"].source == "Отдел"
    assert set(s.origin.values()) == {"heuristic"}


def test_model_only_for_unsure_columns_and_sees_no_data(source: pd.DataFrame) -> None:
    prompts: list[str] = []
    answer = '```json\n{"Табельный номер": "Таб. №", "Код": "Выдуманная колонка"}\n```'
    s = suggest_mapping(source, ["ФИО", "Табельный номер", "Код"], fake_llm(answer, prompts))
    rules = {r.target: r for r in s.spec.columns}
    assert rules["Табельный номер"].source == "Таб. №" and s.origin["Табельный номер"] == "llm"
    # Колонку, которой нет в источнике, модель не может «назначить».
    assert rules["Код"].op == "const" and s.origin["Код"] == "none"
    assert len(prompts) == 1 and "ФИО" not in prompts[0].split("найти источник:")[1]
    for secret in ("Иванов", "Петрова", "7701234567", "12 500"):
        assert secret not in prompts[0]


def test_model_failure_leaves_column_unmapped(source: pd.DataFrame) -> None:
    s = suggest_mapping(source, ["Код"], fake_llm("не JSON", []))
    assert s.spec.columns[0].op == "const" and s.origin["Код"] == "none"
