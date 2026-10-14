"""Сроки ответа по производственному календарю.

Нерабочие дни по умолчанию: суббота, воскресенье и праздники ст. 112 ТК РФ.
Переносы выходных задаются таблицей holidays (её ведёт администратор).
"""

from collections.abc import Mapping
from datetime import date, timedelta

# Нерабочие праздничные дни, ст. 112 ТК РФ: (месяц, день).
_FIXED_HOLIDAYS = {(1, d) for d in range(1, 9)} | {
    (2, 23),
    (3, 8),
    (5, 1),
    (5, 9),
    (6, 12),
    (11, 4),
}

# Срок ответа на обращение гражданина — 30 дней со дня регистрации (ч. 1 ст. 12
# 59-ФЗ), продление — ещё не более чем на 30 дней (ч. 2 ст. 12).
CITIZEN_APPEAL_DAYS = 30
CITIZEN_APPEAL_EXTENSION_DAYS = 30


class Calendar:
    def __init__(self, overrides: Mapping[date, bool] | None = None) -> None:
        """overrides: дата → рабочий ли день (перекрывает правило по умолчанию)."""
        self.overrides = dict(overrides or {})

    def is_workday(self, d: date) -> bool:
        if d in self.overrides:
            return self.overrides[d]
        return d.weekday() < 5 and (d.month, d.day) not in _FIXED_HOLIDAYS

    def next_workday(self, d: date) -> date:
        """d, если он рабочий, иначе ближайший следующий рабочий день."""
        while not self.is_workday(d):
            d += timedelta(days=1)
        return d

    def add_workdays(self, d: date, n: int) -> date:
        while n > 0:
            d += timedelta(days=1)
            n -= int(self.is_workday(d))
        return d


def citizen_appeal_deadline(registered: date, cal: Calendar, extended: bool = False) -> date:
    """Последний день срока ответа на обращение (59-ФЗ).

    Срок считается в календарных днях; если он истекает в нерабочий день,
    последним днём считается ближайший следующий рабочий (по аналогии со
    ст. 193 ГК РФ — так принято в практике органов власти).
    """
    days = CITIZEN_APPEAL_DAYS + (CITIZEN_APPEAL_EXTENSION_DAYS if extended else 0)
    return cal.next_workday(registered + timedelta(days=days))
