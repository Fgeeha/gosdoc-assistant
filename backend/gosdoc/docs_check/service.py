"""Фоновые задачи модуля: проверка документа и индексация редакции НПА."""

import logging
from datetime import UTC, date, datetime
from pathlib import PurePath

from sqlalchemy.orm import Session, sessionmaker

from gosdoc.core.audit import audit
from gosdoc.core.config import Settings
from gosdoc.core.db import Job, StoredFile
from gosdoc.core.files import save_file
from gosdoc.core.storage import FileStorage
from gosdoc.core.text import TextExtractionError, extract_paragraphs
from gosdoc.docs_check.checker import check_document
from gosdoc.docs_check.docx_review import report_docx, reviewed_docx
from gosdoc.docs_check.models import CheckReport, Finding
from gosdoc.docs_check.npa import SearchBackends, index_edition, mark_failed

log = logging.getLogger(__name__)
CHECK_JOB = "docs_check"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _start(sessions: sessionmaker[Session], job_id: int) -> bool:
    with sessions.begin() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != "queued":
            return False
        job.status, job.started_at = "running", datetime.now(UTC)
    return True


def _fail(sessions: sessionmaker[Session], job_id: int, message: str) -> None:
    with sessions.begin() as db:
        job = db.get(Job, job_id)
        if job is not None:
            job.status, job.error, job.finished_at = "failed", message, datetime.now(UTC)


def run_check_job(
    sessions: sessionmaker[Session],
    storage: FileStorage,
    backends: SearchBackends | None,
    s: Settings,
    job_id: int,
) -> None:
    if not _start(sessions, job_id):
        return
    try:
        with sessions.begin() as db:
            job = db.get(Job, job_id)
            source = db.get(StoredFile, job.input_file_id) if job and job.input_file_id else None
            if job is None or source is None or source.organization_id != job.organization_id:
                raise TextExtractionError("Документ удалён (истёк срок хранения)")
            data = storage.get(source.storage_key)
            paragraphs = extract_paragraphs(data, source.original_name, ocr=True)
            if not paragraphs:
                raise TextExtractionError("В документе нет текста")
            result = check_document(
                db, backends, job.organization_id, paragraphs, date.today(), s.llm_max_context
            )
            summary = result.summary()
            stem = PurePath(source.original_name).stem
            files = {}
            for key, name, content in (
                (
                    "result",
                    f"{stem} — с примечаниями.docx",
                    reviewed_docx(data, source.original_name, paragraphs, result.findings),
                ),
                (
                    "report",
                    f"{stem} — отчёт о проверке.docx",
                    report_docx(source.original_name, summary, result.findings),
                ),
            ):
                files[key] = save_file(
                    db,
                    storage,
                    s,
                    organization_id=job.organization_id,
                    owner_id=job.created_by,
                    name=name,
                    mime=DOCX_MIME,
                    data=content,
                ).id
            report = CheckReport(
                organization_id=job.organization_id,
                job_id=job.id,
                summary=summary,
                result_docx_file_id=files["result"],
                report_docx_file_id=files["report"],
                is_draft=True,
            )
            db.add(report)
            db.flush()
            db.add_all(
                Finding(
                    report_id=report.id,
                    kind=f.kind,
                    severity=f.severity,
                    doc_fragment=f.doc_fragment,
                    npa_ref=f.npa_ref,
                    npa_chunk_id=f.chunk_id,
                    norm=f.norm,
                    quote=f.quote,
                    message=f.message,
                    recommendation=f.recommendation,
                )
                for f in result.findings
            )
            job.status, job.finished_at = "done", datetime.now(UTC)
            job.result = {"report_id": report.id, **summary}
            audit(
                db,
                organization_id=job.organization_id,
                user_id=job.created_by,
                action="docs_check.done",
                object_type="job",
                object_id=job.id,
                details=summary,
            )
    except TextExtractionError as e:
        _fail(sessions, job_id, str(e))
    except Exception:
        log.exception("Сбой проверки документа, задача %s", job_id)
        _fail(sessions, job_id, "Внутренняя ошибка проверки, обратитесь к администратору")


def run_index_job(
    sessions: sessionmaker[Session],
    storage: FileStorage,
    backends: SearchBackends,
    s: Settings,
    edition_id: int,
) -> None:
    try:
        index_edition(sessions, storage, backends, s, edition_id)
    except Exception as e:
        log.exception("Сбой индексации редакции %s", edition_id)
        mark_failed(sessions, edition_id, f"{type(e).__name__}: {e}")
