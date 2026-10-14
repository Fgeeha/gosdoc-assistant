"""API модуля 1: нормативная база и проверка документов."""

import datetime as dt
import io
import zipfile
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gosdoc.api.deps import audit_request, current_user, require_roles
from gosdoc.api.files import readable_file
from gosdoc.core.config import get_settings
from gosdoc.core.db import Job, User, get_db
from gosdoc.core.files import FileTooLarge, save_file
from gosdoc.core.queue import Enqueue, enqueue_after_commit, get_enqueue
from gosdoc.core.repository import OrgRepository
from gosdoc.core.storage import FileStorage, get_storage
from gosdoc.core.text import FORMATS, TextExtractionError, extract_paragraphs
from gosdoc.docs_check.models import CheckReport, Finding, NpaAct, NpaChangeLog, NpaEdition
from gosdoc.docs_check.npa import act_label, add_edition, log_change, visible_acts
from gosdoc.docs_check.npa_refs import KINDS, parse_header
from gosdoc.docs_check.service import CHECK_JOB

router = APIRouter(prefix="/api/docs-check", tags=["docs_check"])
npa_router = APIRouter(prefix="/api/npa", tags=["npa"])
npa_admin = require_roles("npa_admin", "sys_admin")
NPA_FORMATS = (".docx", ".html", ".htm", ".pdf")
MAX_ZIP_FILES = 200


class EditionOut(BaseModel):
    id: int
    effective_from: dt.date
    effective_to: dt.date | None
    status: str
    error: str | None
    indexed_at: dt.datetime | None

    model_config = {"from_attributes": True}


class ActOut(BaseModel):
    id: int
    organization_id: int | None
    kind: str
    number: str
    adopted_at: dt.date | None
    title: str
    repealed_at: dt.date | None
    repealed_note: str
    label: str
    editions: list[EditionOut]


class ActPatch(BaseModel):
    title: str | None = Field(default=None, max_length=1000)
    repealed_at: dt.date | None = None
    repealed_note: str = Field(default="", max_length=2000)


class UploadOut(BaseModel):
    name: str
    ok: bool
    message: str
    act_id: int | None = None
    edition_id: int | None = None


class CheckIn(BaseModel):
    file_ids: list[int] = Field(min_length=1, max_length=50)


class FindingOut(BaseModel):
    id: int
    kind: str
    severity: str
    doc_fragment: str
    npa_ref: str
    norm: str
    quote: str
    message: str
    recommendation: str
    user_verdict: str | None

    model_config = {"from_attributes": True}


class ReportOut(BaseModel):
    id: int
    job_id: int
    summary: dict[str, Any]
    result_docx_file_id: int | None
    report_docx_file_id: int | None
    is_draft: bool
    findings: list[FindingOut]


class VerdictIn(BaseModel):
    verdict: str = Field(pattern="^(accepted|rejected)$")


def _act_out(db: Session, act: NpaAct) -> ActOut:
    editions = db.scalars(
        select(NpaEdition).where(NpaEdition.act_id == act.id).order_by(NpaEdition.effective_from)
    )
    return ActOut(
        id=act.id,
        organization_id=act.organization_id,
        kind=act.kind,
        number=act.number,
        adopted_at=act.adopted_at,
        title=act.title,
        repealed_at=act.repealed_at,
        repealed_note=act.repealed_note,
        label=act_label(act),
        editions=[EditionOut.model_validate(e) for e in editions],
    )


def _visible_act(db: Session, user: User, act_id: int) -> NpaAct:
    act = db.scalar(select(NpaAct).where(NpaAct.id == act_id, visible_acts(user.organization_id)))
    if act is None:
        raise HTTPException(404, "Акт не найден")
    return act


def _editable_act(db: Session, user: User, act_id: int) -> NpaAct:
    act = _visible_act(db, user, act_id)
    # Общие акты ведёт администратор системы, ведомственные — администратор НПА организации.
    if act.organization_id is None and "sys_admin" not in user.roles:
        raise HTTPException(403, "Общие акты изменяет администратор системы")
    return act


# --- Нормативная база --------------------------------------------------------------


@npa_router.get("/acts", response_model=list[ActOut])
def list_acts(
    db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> list[ActOut]:
    acts = db.scalars(
        select(NpaAct)
        .where(visible_acts(user.organization_id))
        .order_by(NpaAct.kind, NpaAct.number)
    )
    return [_act_out(db, a) for a in acts]


@npa_router.get("/kinds")
def kinds(_: User = Depends(current_user)) -> dict[str, str]:
    return KINDS


def _unpack(name: str, data: bytes) -> list[tuple[str, bytes]]:
    if PurePath(name).suffix.lower() != ".zip":
        return [(name, data)]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [
                m
                for m in z.infolist()
                if not m.is_dir() and PurePath(m.filename).suffix.lower() in NPA_FORMATS
            ]
            if len(members) > MAX_ZIP_FILES:
                raise HTTPException(400, f"В архиве больше {MAX_ZIP_FILES} файлов")
            limit = get_settings().storage_max_file_mb * 1024 * 1024
            if any(m.file_size > limit for m in members):
                raise HTTPException(413, "Файл в архиве больше допустимого размера")
            return [(PurePath(m.filename).name, z.read(m)) for m in members]
    except zipfile.BadZipFile as e:
        raise HTTPException(400, f"{name}: архив повреждён") from e


@npa_router.post("/upload", response_model=list[UploadOut])
def upload_npa(
    request: Request,
    files: list[UploadFile],
    shared: bool = Form(False),
    effective_from: dt.date | None = Form(None),
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(npa_admin),
    storage: FileStorage = Depends(get_storage),
    enqueue: Enqueue = Depends(get_enqueue),
) -> list[UploadOut]:
    """DOCX/HTML/PDF по одному, пакетом или zip-архивом. Индексация — в фоне."""
    if shared and "sys_admin" not in user.roles:
        raise HTTPException(403, "Общие акты загружает администратор системы")
    s = get_settings()
    out: list[UploadOut] = []
    queued: list[int] = []
    for upload in files:
        raw = upload.file.read(s.storage_max_file_mb * 1024 * 1024 + 1)
        for name, data in _unpack(upload.filename or "file", raw):
            try:
                paragraphs = extract_paragraphs(data, name, ocr=False)
                header = parse_header(paragraphs)
                if header is None:
                    raise TextExtractionError(
                        "не удалось определить вид, номер и дату акта по заголовку"
                    )
                stored = save_file(
                    db,
                    storage,
                    s,
                    organization_id=user.organization_id,
                    owner_id=user.id,
                    name=name,
                    mime="application/octet-stream",
                    data=data,
                    contains_pdn=False,
                )
                res = add_edition(
                    db,
                    file=stored,
                    paragraphs=paragraphs,
                    header=header,
                    organization_id=None if shared else user.organization_id,
                    user_id=user.id,
                    effective_from=effective_from,
                )
            except (TextExtractionError, FileTooLarge) as e:
                out.append(UploadOut(name=name, ok=False, message=str(e)))
                continue
            queued.append(res.edition.id)
            what = "новый акт" if res.created_act else "новая редакция"
            out.append(
                UploadOut(
                    name=name,
                    ok=True,
                    act_id=res.act.id,
                    edition_id=res.edition.id,
                    message=f"{act_label(res.act)}: {what} с {res.edition.effective_from:%d.%m.%Y}",
                )
            )
    audit_request(
        db, request, user, "npa.uploaded", details={"files": len(out), "queued": len(queued)}
    )
    enqueue_after_commit(db, enqueue, "npa", "index_npa_edition", queued)
    return out


@npa_router.patch("/acts/{act_id}", response_model=ActOut)
def patch_act(
    act_id: int,
    body: ActPatch,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(npa_admin),
) -> ActOut:
    act = _editable_act(db, user, act_id)
    if body.title is not None:
        act.title = body.title
    if body.repealed_at != act.repealed_at:
        act.repealed_at, act.repealed_note = body.repealed_at, body.repealed_note
        log_change(
            db,
            act,
            "repealed" if body.repealed_at else "restored",
            user.id,
            repealed_at=body.repealed_at.isoformat() if body.repealed_at else None,
            note=body.repealed_note,
        )
    audit_request(db, request, user, "npa.act_updated", object_type="npa_act", object_id=act.id)
    return _act_out(db, act)


@npa_router.post("/acts/{act_id}/reindex", status_code=202)
def reindex_act(
    act_id: int,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(npa_admin),
    enqueue: Enqueue = Depends(get_enqueue),
) -> dict[str, int]:
    act = _editable_act(db, user, act_id)
    ids = [e.id for e in db.scalars(select(NpaEdition).where(NpaEdition.act_id == act.id))]
    for e in db.scalars(select(NpaEdition).where(NpaEdition.id.in_(ids))):
        e.status = "queued"
    log_change(db, act, "reindexed", user.id)
    audit_request(db, request, user, "npa.reindex", object_type="npa_act", object_id=act.id)
    enqueue_after_commit(db, enqueue, "npa", "index_npa_edition", ids)
    return {"editions": len(ids)}


@npa_router.get("/changes")
def change_log(
    limit: int = 200,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    stmt = (
        select(NpaChangeLog, NpaAct)
        .join(NpaAct, NpaAct.id == NpaChangeLog.act_id)
        .where(visible_acts(user.organization_id))
        .order_by(NpaChangeLog.id.desc())
        .limit(min(limit, 1000))
    )
    return [
        {"at": c.at, "act_id": a.id, "act": act_label(a), "change": c.change, "details": c.details}
        for c, a in db.execute(stmt)
    ]


# --- Проверка документов -------------------------------------------------------------


@router.post("/jobs", status_code=201)
def create_checks(
    body: CheckIn,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    enqueue: Enqueue = Depends(get_enqueue),
) -> list[dict[str, Any]]:
    repo = OrgRepository(Job, db, user.organization_id)
    jobs = []
    for file_id in dict.fromkeys(body.file_ids):
        f = readable_file(db, user, file_id)
        if PurePath(f.original_name).suffix.lower() not in FORMATS:
            raise HTTPException(
                400, f"{f.original_name}: формат не поддерживается ({', '.join(FORMATS)})"
            )
        jobs.append(
            repo.add(
                Job(
                    kind=CHECK_JOB,
                    status="queued",
                    input_file_id=f.id,
                    params={},
                    result={},
                    created_by=user.id,
                )
            )
        )
    for job in jobs:
        audit_request(db, request, user, "docs_check.created", object_type="job", object_id=job.id)
    ids = [j.id for j in jobs]
    enqueue_after_commit(db, enqueue, "docs_check", "process_docs_check", ids)
    return [{"id": j.id, "status": j.status, "input_file_id": j.input_file_id} for j in jobs]


@router.get("/jobs")
def list_checks(
    db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> list[dict[str, Any]]:
    jobs = OrgRepository(Job, db, user.organization_id).list(
        Job.kind == CHECK_JOB, Job.created_by == user.id, order_by=Job.id.desc(), limit=100
    )
    return [
        {
            "id": j.id,
            "status": j.status,
            "input_file_id": j.input_file_id,
            "result": j.result,
            "error": j.error,
            "created_at": j.created_at,
        }
        for j in jobs
    ]


def _report(db: Session, user: User, job_id: int) -> CheckReport:
    job = OrgRepository(Job, db, user.organization_id).get(job_id)
    report = db.scalar(select(CheckReport).where(CheckReport.job_id == job_id)) if job else None
    if job is None or job.kind != CHECK_JOB or job.created_by != user.id or report is None:
        raise HTTPException(404, "Отчёт не найден")
    return report


@router.get("/jobs/{job_id}/report", response_model=ReportOut)
def get_report(
    job_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> ReportOut:
    report = _report(db, user, job_id)
    findings = db.scalars(
        select(Finding).where(Finding.report_id == report.id).order_by(Finding.id)
    )
    return ReportOut(
        id=report.id,
        job_id=report.job_id,
        summary=report.summary,
        result_docx_file_id=report.result_docx_file_id,
        report_docx_file_id=report.report_docx_file_id,
        is_draft=report.is_draft,
        findings=[FindingOut.model_validate(f) for f in findings],
    )


@router.post("/jobs/{job_id}/findings/{finding_id}/verdict", response_model=FindingOut)
def set_verdict(
    job_id: int,
    finding_id: int,
    body: VerdictIn,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
) -> Finding:
    """Оценка замечания сотрудником — основа для мониторинга качества."""
    report = _report(db, user, job_id)
    finding = db.get(Finding, finding_id)
    if finding is None or finding.report_id != report.id:
        raise HTTPException(404, "Замечание не найдено")
    finding.user_verdict = body.verdict
    audit_request(
        db,
        request,
        user,
        "docs_check.verdict",
        object_type="finding",
        object_id=finding.id,
        details={"verdict": body.verdict, "kind": finding.kind},
    )
    return finding
