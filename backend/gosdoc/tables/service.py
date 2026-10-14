"""Выполнение задачи переноса: источник + маппинг + шаблон → результат и отчёт."""

import logging
from datetime import UTC, datetime
from pathlib import PurePath

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from gosdoc.core.audit import audit
from gosdoc.core.config import Settings
from gosdoc.core.db import Job, StoredFile
from gosdoc.core.files import save_file
from gosdoc.core.storage import FileNotFoundInStorage, FileStorage
from gosdoc.tables.spec import MappingSpec
from gosdoc.tables.tableio import XLSX_MIME, TableReadError, read_table, write_report, write_result
from gosdoc.tables.transforms import SpecError, apply_mapping

log = logging.getLogger(__name__)
KIND = "table"


class InputMissing(ValueError):
    pass


def _file(db: Session, job: Job, file_id: int | None) -> StoredFile:
    f = db.get(StoredFile, file_id) if file_id else None
    if f is None or f.organization_id != job.organization_id:
        raise InputMissing("Исходный файл или шаблон удалён (истёк срок хранения)")
    return f


def _process(db: Session, storage: FileStorage, s: Settings, job: Job) -> None:
    source = _file(db, job, job.input_file_id)
    template = _file(db, job, job.params.get("template_file_id"))
    spec = MappingSpec.model_validate(job.params["spec"])
    df = read_table(storage.get(source.storage_key), source.original_name)
    result = apply_mapping(spec, df)
    stem = PurePath(source.original_name).stem
    saved = {}
    for key, name, data in (
        (
            "result_file_id",
            f"{stem} — результат.xlsx",
            write_result(result, storage.get(template.storage_key), template.original_name),
        ),
        ("report_file_id", f"{stem} — отчёт.xlsx", write_report(result, list(df.columns))),
    ):
        f = save_file(
            db,
            storage,
            s,
            organization_id=job.organization_id,
            owner_id=job.created_by,
            name=name,
            mime=XLSX_MIME,
            data=data,
        )
        saved[key] = f.id
    job.result = {**result.summary(), **saved}
    job.status, job.finished_at = "done", datetime.now(UTC)
    audit(
        db,
        organization_id=job.organization_id,
        user_id=job.created_by,
        action="tables.job_done",
        object_type="job",
        object_id=job.id,
        details=job.result,
    )


def run_table_job(
    sessions: sessionmaker[Session], storage: FileStorage, s: Settings, job_id: int
) -> None:
    """Ошибки данных и маппинга записываются в задачу, а не бросаются наружу."""
    with sessions.begin() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != "queued":  # повторная доставка сообщения
            return
        job.status, job.started_at = "running", datetime.now(UTC)
    try:
        with sessions.begin() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            _process(db, storage, s, job)
    except (TableReadError, SpecError, InputMissing, FileNotFoundInStorage, ValidationError) as e:
        _fail(sessions, job_id, str(e)[:2000])
    except Exception:
        log.exception("Сбой задачи переноса %s", job_id)
        _fail(sessions, job_id, "Внутренняя ошибка обработки, обратитесь к администратору")


def _fail(sessions: sessionmaker[Session], job_id: int, message: str) -> None:
    with sessions.begin() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status, job.error, job.finished_at = "failed", message, datetime.now(UTC)
        audit(
            db,
            organization_id=job.organization_id,
            user_id=job.created_by,
            action="tables.job_failed",
            object_type="job",
            object_id=job.id,
        )
