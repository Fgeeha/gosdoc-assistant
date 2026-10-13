"""Подтверждённый пользователем маппинг для фикстур tests/fixtures/tables."""

from pathlib import Path

from gosdoc.tables.spec import MappingSpec

FIXTURES = Path(__file__).parent / "fixtures" / "tables"

SPEC = MappingSpec.model_validate(
    {
        "columns": [
            {"target": "ФИО", "op": "fio_join", "sources": ["Фамилия", "Имя", "Отчество"]},
            {"target": "Дата рождения", "op": "date", "source": "ДР"},
            {
                "target": "Пол",
                "op": "dict",
                "source": "Пол",
                "map": {"м": "Мужской", "ж": "Женский"},
            },
            {"target": "ИНН", "op": "copy", "source": "ИНН"},
            {"target": "СНИЛС", "op": "copy", "source": "СНИЛС"},
            {"target": "Сумма", "op": "money", "source": "Сумма, руб."},
            {"target": "Подразделение", "op": "copy", "source": "Отдел"},
        ],
        "filter": [{"column": "Статус", "op": "eq", "value": "активен"}],
        "validate": {"ИНН": ["required", "inn"], "СНИЛС": ["snils"]},
    }
)
# Ожидаемый результат: 11 строк данных (пустая не считается) = 4 + 2 + 5.
EXPECTED = {"rows_in": 11, "rows_out": 4, "rows_filtered": 2, "rows_rejected": 5, "rows_written": 4}
