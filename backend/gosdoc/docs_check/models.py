"""Таблицы модуля проверки документов и нормативной базы."""

import datetime as dt
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gosdoc.core.db import Base


class NpaAct(Base):
    __tablename__ = "npa_acts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # None — общий акт (федеральный), иначе ведомственный акт организации.
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    kind: Mapped[str] = mapped_column(
        String(30)
    )  # federal_law, government_decree, order, gost, ...
    number: Mapped[str] = mapped_column(String(100))
    adopted_at: Mapped[dt.date | None] = mapped_column(Date)
    title: Mapped[str] = mapped_column(Text, default="")
    issuer: Mapped[str] = mapped_column(String(300), default="")
    repealed_at: Mapped[dt.date | None] = mapped_column(Date)
    repealed_note: Mapped[str] = mapped_column(Text, default="")  # чем и когда отменён
    canonical_key: Mapped[str] = mapped_column(String(200), index=True)


class NpaEdition(Base):
    __tablename__ = "npa_editions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(ForeignKey("npa_acts.id", ondelete="CASCADE"), index=True)
    effective_from: Mapped[dt.date] = mapped_column(Date)
    effective_to: Mapped[dt.date | None] = mapped_column(Date)
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"))
    text_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued/indexed/failed
    error: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class NpaChunk(Base):
    """Фрагмент редакции; его id — идентификатор в Elasticsearch и векторном индексе."""

    __tablename__ = "npa_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edition_id: Mapped[int] = mapped_column(
        ForeignKey("npa_editions.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(100))  # «ст. 12 ч. 1»
    article: Mapped[str] = mapped_column(String(20), default="")
    heading: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)


class NpaChangeLog(Base):
    __tablename__ = "npa_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(ForeignKey("npa_acts.id", ondelete="CASCADE"), index=True)
    change: Mapped[str] = mapped_column(String(30))  # added / new_edition / repealed / reindexed
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CheckReport(Base):
    __tablename__ = "check_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result_docx_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    report_docx_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    is_draft: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("check_reports.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # npa_ref / content
    severity: Mapped[str] = mapped_column(String(20))  # critical / warning / info
    doc_fragment: Mapped[str] = mapped_column(Text)
    npa_ref: Mapped[str] = mapped_column(Text, default="")  # как ссылка записана в документе
    npa_chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("npa_chunks.id", ondelete="SET NULL")
    )
    norm: Mapped[str] = mapped_column(
        Text, default=""
    )  # «ст. 12 ч. 1 Федерального закона … № 59-ФЗ»
    quote: Mapped[str] = mapped_column(Text, default="")  # дословно из npa_chunks.text
    message: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    user_verdict: Mapped[str | None] = mapped_column(String(20))  # accepted / rejected
