"""Проверки значений: обязательность, типы, контрольные суммы ИНН/СНИЛС/ОГРН.

Проверяется уже преобразованное значение (строка). Пустое значение проверяет
только правило `required`.
"""

import re
from collections.abc import Callable
from datetime import datetime


def _digits(value: str) -> str:
    return re.sub(r"[\s-]", "", value)


def inn_ok(value: str) -> bool:
    d = _digits(value)
    if not d.isdigit() or len(d) not in (10, 12):
        return False
    n = [int(c) for c in d]

    def control(weights: tuple[int, ...]) -> int:
        return sum(w * x for w, x in zip(weights, n, strict=False)) % 11 % 10

    if len(n) == 10:
        return control((2, 4, 10, 3, 5, 9, 4, 6, 8)) == n[9]
    return (
        control((7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == n[10]
        and control((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == n[11]
    )


def snils_ok(value: str) -> bool:
    d = _digits(value)
    if not d.isdigit() or len(d) != 11:
        return False
    number, control = d[:9], int(d[9:])
    if int(number) <= 1001998:  # для номеров до 001-001-998 контрольное число не считается
        return True
    expected = sum(int(c) * (9 - i) for i, c in enumerate(number)) % 101
    return (0 if expected == 100 else expected) == control


def ogrn_ok(value: str) -> bool:
    d = _digits(value)
    if not d.isdigit() or len(d) not in (13, 15):
        return False
    modulo = 11 if len(d) == 13 else 13  # ОГРН / ОГРНИП
    return int(d[:-1]) % modulo % 10 == int(d[-1])


def _number_ok(value: str) -> bool:
    try:
        float(value.replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return False
    return True


def _date_ok(value: str) -> bool:
    try:
        datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return False
    return True


_CHECKS: dict[str, tuple[Callable[[str], bool], str]] = {
    "inn": (inn_ok, "неверный ИНН (контрольная сумма)"),
    "snils": (snils_ok, "неверный СНИЛС (контрольная сумма)"),
    "ogrn": (ogrn_ok, "неверный ОГРН/ОГРНИП (контрольная сумма)"),
    "int": (lambda v: _digits(v).removeprefix("-").isdigit(), "ожидается целое число"),
    "number": (_number_ok, "ожидается число"),
    "date": (_date_ok, "ожидается дата ДД.ММ.ГГГГ"),
}


def validate_value(value: str, rules: list[str]) -> list[str]:
    """Ошибки значения по правилам колонки (пустой список — значение корректно)."""
    if not value.strip():
        return ["обязательное поле не заполнено"] if "required" in rules else []
    errors = []
    for rule in rules:
        if rule == "required":
            continue
        ok, message = _CHECKS[rule]
        if not ok(value):
            errors.append(message)
    return errors
