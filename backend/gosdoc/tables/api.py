"""API модуля 3: предложение маппинга, предпросмотр, шаблоны маппинга, задачи."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from gosdoc.api.deps import audit_request, current_user
from gosdoc.api.files import readable_file
from gosdoc.core.config import get_settings
from gosdoc.core.db import Job, StoredFile, User, get_db
from gosdoc.core.files import save_file
from gosdoc.core.llm import LLMClient
from gosdoc.core.queue import Enqueue, enqueue_after_commit, get_enqueue
from gosdoc.core.repository import OrgRepository
from gosdoc.core.storage import FileStorage, get_storage
from gosdoc.tables.mapping import Origin, suggest_mapping
from gosdoc.tables.models import TableMapping
from gosdoc.tables.service import KIND
from gosdoc.tables.spec import MappingSpec
from gosdoc.tables.tableio import (
    XLSX_MIME,
    TableReadError,
    header_only_template,
    read_columns,
    read_table,
)
from gosdoc.tables.transforms import SpecError, apply_mapping

router = APIRouter(prefix="/api/tables", tags=["tables"])
PREVIEW_ROWS = 50
MAX_BATCH = 50


def get_llm() -> LLMClient:
    return LLMClient(get_settings())


class SuggestIn(BaseModel):
    source_file_id: int
    template_file_id: int


class SuggestOut(BaseModel):
    source_columns: list[str]
    target_columns: list[str]
    preview: list[dict[str, str]]
    spec: dict[str, Any]
    confidence: dict[str, float]
    origin: dict[str, Origin]
    # Предложение маппинга — черновик: пользователь проверяет и подтверждает.
    is_draft: bool = True


class PreviewIn(BaseModel):
    source_file_id: int
    spec: MappingSpec


class PreviewOut(BaseModel):
    rows: list[dict[str, str]]
    issues: list[dict[str, Any]]
    summary: dict[str, int]


class MappingIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    spec: MappingSpec
    template_file_id: int
    shared: bool = False


class MappingOut(BaseModel):
    id: int
    name: str
    spec: dict[str, Any]
    target_template_file_id: int
    owner_id: int | None
    shared: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobsIn(BaseModel):
    source_file_ids: list[int] = Field(min_length=1, max_length=MAX_BATCH)
    mapping_id: int | None = None  # сохранённый маппинг или spec + template_file_id
    spec: MappingSpec | None = None
    template_file_id: int | None = None


class JobOut(BaseModel):
    id: int
    status: str
    input_file_id: int | None
    params: dict[str, Any]
    result: dict[str, Any]
    error: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


def _read(storage: FileStorage, f: StoredFile, nrows: int | None = None) -> Any:
    try:
        return read_table(storage.get(f.storage_key), f.original_name, nrows=nrows)
    except TableReadError as e:
        raise HTTPException(400, f"{f.original_name}: {e}") from e


@router.post("/suggest", response_model=SuggestOut)
def suggest(
    body: SuggestIn,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
    llm: LLMClient = Depends(get_llm),
) -> SuggestOut:
    source = readable_file(db, user, body.source_file_id)
    template = readable_file(db, user, body.template_file_id)
    df = _read(storage, source)
    try:
        targets = read_columns(storage.get(template.storage_key), template.original_name)
    except TableReadError as e:
        raise HTTPException(400, f"Шаблон: {e}") from e
    s = suggest_mapping(df, targets, llm)
    return SuggestOut(
        source_columns=list(df.columns),
        target_columns=targets,
        preview=[{str(k): str(v) for k, v in r.items()} for r in df.head(5).to_dict("records")],
        spec=s.spec.model_dump(),
        confidence=s.confidence,
        origin=s.origin,
    )


@router.post("/preview", response_model=PreviewOut)
def preview(
    body: PreviewIn,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
) -> PreviewOut:
    """Результат на первых строках — до запуска задачи на всём файле."""
    df = _read(storage, readable_file(db, user, body.source_file_id), nrows=PREVIEW_ROWS)
    try:
        result = apply_mapping(body.spec, df)
    except SpecError as e:
        raise HTTPException(400, str(e)) from e
    return PreviewOut(
        rows=[{str(k): str(v) for k, v in r.items()} for r in result.data.to_dict("records")],
        issues=[{"row": i.row, "status": i.status, "reason": i.reason} for i in result.issues],
        summary=result.summary(),
    )


# --- Сохранённые маппинги -----------------------------------------------------


def _visible_mapping(db: Session, user: User, mapping_id: int) -> TableMapping:
    m = OrgRepository(TableMapping, db, user.organization_id).get(mapping_id)
    if m is None or not (m.shared or m.owner_id == user.id):
        raise HTTPException(404, "Маппинг не найден")
    return m


def _editable_mapping(db: Session, user: User, mapping_id: int) -> TableMapping:
    m = _visible_mapping(db, user, mapping_id)
    if m.owner_id != user.id and "sys_admin" not in user.roles:
        raise HTTPException(403, "Изменить маппинг может только его автор")
    return m


def _clean_template(
    db: Session, user: User, storage: FileStorage, template_file_id: int
) -> StoredFile:
    """Шаблон для хранения вместе с маппингом: только заголовок, без срока хранения."""
    f = readable_file(db, user, template_file_id)
    try:
        data = header_only_template(storage.get(f.storage_key), f.original_name)
    except TableReadError as e:
        raise HTTPException(400, f"Шаблон: {e}") from e
    name = f.original_name.rsplit(".", 1)[0] + ".xlsx"
    return save_file(
        db,
        storage,
        get_settings(),
        organization_id=user.organization_id,
        owner_id=user.id,
        name=name,
        mime=XLSX_MIME,
        data=data,
        contains_pdn=False,
    )


@router.get("/mappings", response_model=list[MappingOut])
def list_mappings(
    db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> list[TableMapping]:
    visible = or_(TableMapping.shared.is_(True), TableMapping.owner_id == user.id)
    return OrgRepository(TableMapping, db, user.organization_id).list(
        visible, order_by=TableMapping.name, limit=500
    )


@router.post("/mappings", response_model=MappingOut, status_code=201)
def create_mapping(
    body: MappingIn,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
) -> TableMapping:
    template = _clean_template(db, user, storage, body.template_file_id)
    m = OrgRepository(TableMapping, db, user.organization_id).add(
        TableMapping(
            name=body.name,
            spec=body.spec.model_dump(),
            target_template_file_id=template.id,
            owner_id=user.id,
            shared=body.shared,
        )
    )
    audit_request(db, request, user, "tables.mapping_saved", object_type="mapping", object_id=m.id)
    return m


@router.put("/mappings/{mapping_id}", response_model=MappingOut)
def update_mapping(
    mapping_id: int,
    body: MappingIn,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
) -> TableMapping:
    m = _editable_mapping(db, user, mapping_id)
    if body.template_file_id != m.target_template_file_id:
        m.target_template_file_id = _clean_template(db, user, storage, body.template_file_id).id
    m.name, m.spec, m.shared = body.name, body.spec.model_dump(), body.shared
    db.flush()
    audit_request(db, request, user, "tables.mapping_saved", object_type="mapping", object_id=m.id)
    return m


@router.delete("/mappings/{mapping_id}", status_code=204)
def delete_mapping(
    mapping_id: int,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
) -> None:
    m = _editable_mapping(db, user, mapping_id)
    db.delete(m)
    audit_request(
        db, request, user, "tables.mapping_deleted", object_type="mapping", object_id=m.id
    )


# --- Задачи ------------------------------------------------------------------


@router.post("/jobs", response_model=list[JobOut], status_code=201)
def create_jobs(
    body: JobsIn,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    enqueue: Enqueue = Depends(get_enqueue),
) -> list[Job]:
    """Пакетная обработка: одна задача на каждый исходный файл."""
    if body.mapping_id is not None:
        m = _visible_mapping(db, user, body.mapping_id)
        spec, template_id = m.spec, m.target_template_file_id
    elif body.spec is not None and body.template_file_id is not None:
        spec, template_id = body.spec.model_dump(), body.template_file_id
    else:
        raise HTTPException(400, "Укажите сохранённый маппинг или spec и template_file_id")
    template = db.get(StoredFile, template_id)
    if template is None or template.organization_id != user.organization_id:
        raise HTTPException(404, "Шаблон не найден")
    if body.mapping_id is None:
        readable_file(db, user, template_id)

    repo = OrgRepository(Job, db, user.organization_id)
    jobs = []
    for file_id in dict.fromkeys(body.source_file_ids):
        readable_file(db, user, file_id)
        job = repo.add(
            Job(
                kind=KIND,
                status="queued",
                input_file_id=file_id,
                params={
                    "spec": spec,
                    "template_file_id": template_id,
                    "mapping_id": body.mapping_id,
                },
                result={},
                created_by=user.id,
            )
        )
        audit_request(db, request, user, "tables.job_created", object_type="job", object_id=job.id)
        jobs.append(job)
    enqueue_after_commit(db, enqueue, "tables", "process_table_job", [job.id for job in jobs])
    return jobs


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> list[Job]:
    return OrgRepository(Job, db, user.organization_id).list(
        Job.kind == KIND, Job.created_by == user.id, order_by=Job.id.desc(), limit=100
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> Job:
    job = OrgRepository(Job, db, user.organization_id).get(job_id)
    if job is None or job.kind != KIND or job.created_by != user.id:
        raise HTTPException(404, "Задача не найдена")
    return job
