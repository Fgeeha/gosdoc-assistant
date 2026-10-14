"""Журнал аудита: кто, что, когда. Таблица append-only (триггер в миграции)."""

from typing import Any

from sqlalchemy.orm import Session

from gosdoc.core.db import AuditLog
from gosdoc.core.logging import mask_obj


def audit(
    db: Session,
    *,
    organization_id: int,
    action: str,
    user_id: int | None = None,
    object_type: str | None = None,
    object_id: object = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Добавляет запись в текущую транзакцию; ПДн в details маскируются."""
    db.add(
        AuditLog(
            organization_id=organization_id,
            user_id=user_id,
            ip=ip,
            action=action,
            object_type=object_type,
            object_id=None if object_id is None else str(object_id),
            details=mask_obj(details or {}),
        )
    )
