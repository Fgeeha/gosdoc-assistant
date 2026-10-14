"""Таблицы модуля писем."""

import datetime as dt
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gosdoc.core.db import Base


class LetterBlank(Base):
    """Бланк организации: DOCX-шаблон docxtpl, загружает администратор."""

    __tablename__ = "letter_blanks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    template_file_id: Mapped[int] = mapped_column(ForeignKey("files.id"))
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # переменные шаблона
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Letter(Base):
    __tablename__ = "letters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    blank_id: Mapped[int | None] = mapped_column(ForeignKey("letter_blanks.id"))
    incoming_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    # Реквизиты: номер и дата вводятся вручную (СЭД не подключена).
    outgoing_number: Mapped[str | None] = mapped_column(String(100))
    date: Mapped[dt.date | None] = mapped_column(Date)
    addressee: Mapped[str] = mapped_column(Text, default="")
    executor: Mapped[str] = mapped_column(String(300), default="")
    deadline: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft / approved
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    requisites: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LetterVersion(Base):
    __tablename__ = "letter_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    letter_id: Mapped[int] = mapped_column(ForeignKey("letters.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)  # title, greeting, paragraphs, closing
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # None — черновик модели
    docx_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    pdf_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # model_name, prompt_version
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Holiday(Base):
    """Производственный календарь (федеральный, общий для всех организаций).

    По умолчанию нерабочие — сб, вс и праздники ст. 112 ТК РФ; таблица хранит
    отклонения: перенесённые выходные (is_workday=False) и рабочие дни
    на месте выходных (is_workday=True). Ведёт администратор системы.
    """

    __tablename__ = "holidays"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    is_workday: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(200), default="")
