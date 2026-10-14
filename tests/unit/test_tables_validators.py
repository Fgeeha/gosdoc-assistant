import pytest
from gosdoc.tables.validators import inn_ok, ogrn_ok, snils_ok, validate_value


@pytest.mark.parametrize("value", ["7707083893", "500100732259", "7707 083 893"])
def test_inn_valid(value: str) -> None:
    assert inn_ok(value)


@pytest.mark.parametrize("value", ["7707083894", "500100732258", "123", "77070838ab"])
def test_inn_invalid(value: str) -> None:
    assert not inn_ok(value)


def test_snils_and_ogrn() -> None:
    assert snils_ok("112-233-445 95") and snils_ok("11223344595")
    assert not snils_ok("112-233-445 96")
    assert snils_ok("001-001-998 00")  # малые номера без контрольного числа
    assert ogrn_ok("1027700132195") and ogrn_ok("304500116000157")
    assert not ogrn_ok("1027700132196")


def test_validate_value_rules() -> None:
    assert validate_value("", ["required", "inn"]) == ["обязательное поле не заполнено"]
    assert validate_value("", ["inn"]) == []  # пустое проверяет только required
    assert validate_value("7707083894", ["inn"]) == ["неверный ИНН (контрольная сумма)"]
    assert validate_value("12,5", ["number"]) == []
    assert validate_value("12.5", ["int"]) == ["ожидается целое число"]
    assert validate_value("31.02.2024", ["date"]) == ["ожидается дата ДД.ММ.ГГГГ"]
