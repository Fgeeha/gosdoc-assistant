"""Ожидание зависимостей прерывается на ошибке аутентификации, а не ждёт до таймаута."""

import pytest
from gosdoc.core import health
from gosdoc.core.config import Settings


def test_wait_stops_on_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake(s: Settings, only: set[str] | None = None) -> dict[str, str]:
        calls.append(1)
        return {"postgres": 'OperationalError: FATAL:  password authentication failed for user "x"'}

    monkeypatch.setattr(health, "check_dependencies", fake)
    monkeypatch.setattr(health.time, "sleep", lambda _: None)
    assert health.wait_for_dependencies(Settings(), timeout_s=60) is False
    assert len(calls) == 1
