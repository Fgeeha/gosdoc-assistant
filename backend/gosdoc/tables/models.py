"""Таблицы модуля переноса данных."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gosdoc.core.db import Base


class TableMapping(Base):
    """Сохранённый маппинг: спецификация и шаблон (только заголовок, без данных)."""

    __tablename__ = "table_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_template_file_id: Mapped[int] = mapped_column(ForeignKey("files.id"))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    shared: Mapped[bool] = mapped_column(Boolean, default=False)  # доступен всей организации
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
