from datetime import date

from gosdoc.letters.deadlines import Calendar, citizen_appeal_deadline
from gosdoc.letters.style_check import check_style


def test_calendar_defaults_and_overrides() -> None:
    cal = Calendar()
    assert not cal.is_workday(date(2026, 1, 5))  # новогодние каникулы, ст. 112 ТК РФ
    assert not cal.is_workday(date(2026, 10, 17))  # суббота
    assert cal.is_workday(date(2026, 10, 16))
    moved = Calendar({date(2026, 10, 16): False, date(2026, 10, 17): True})
    assert not moved.is_workday(date(2026, 10, 16)) and moved.is_workday(date(2026, 10, 17))
    assert cal.add_workdays(date(2026, 10, 16), 1) == date(2026, 10, 19)


def test_citizen_appeal_deadline() -> None:
    cal = Calendar()
    assert citizen_appeal_deadline(date(2026, 10, 12), cal) == date(2026, 11, 11)
    # 30 дней от 05.10.2026 — 04.11.2026, праздник: срок переносится на 05.11.
    assert citizen_appeal_deadline(date(2026, 10, 5), cal) == date(2026, 11, 5)
    # 30 дней от 17.09.2026 — 17.10.2026, суббота: переносится на понедельник.
    assert citizen_appeal_deadline(date(2026, 9, 17), cal) == date(2026, 10, 19)
    assert citizen_appeal_deadline(date(2026, 10, 12), cal, extended=True) == date(2026, 12, 11)


def test_style_check() -> None:
    issues = check_style(
        [
            "Доводим до вашего сведения, что дорога щас ремонтируется!",
            "Ремонт будет выполнен в ближайшее время.",
            "Сообщаем, что улица включена в план ремонта на 2027 год.",
            " ".join(["слово"] * 45) + ".",
        ]
    )
    messages = [(i.paragraph, i.message) for i in issues]
    assert any(p == 0 and "доводим до вашего сведения" in m for p, m in messages)
    assert any(p == 0 and "«щас»" in m for p, m in messages)
    assert any(p == 0 and "восклицательный" in m for p, m in messages)
    assert any(p == 1 and "конкретный срок" in m for p, m in messages)
    assert not any(p == 2 for p, _ in messages)
    assert any(p == 3 and "длинное предложение (45 слов)" in m for p, m in messages)
