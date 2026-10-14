"""API модуля 2: письма, версии, проверки, DOCX/PDF, бланки."""

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gosdoc.api.deps import audit_request, current_user, require_roles
from gosdoc.api.files import can_view, readable_file
from gosdoc.core.config import get_settings
from gosdoc.core.db import Job, User, get_db
from gosdoc.core.llm import LLMClient
from gosdoc.core.queue import Enqueue, enqueue_after_commit, get_enqueue
from gosdoc.core.repository import OrgRepository
from gosdoc.core.storage import FileStorage, get_storage
from gosdoc.core.text import TextExtractionError, extract_text
from gosdoc.letters import service
from gosdoc.letters.gost import CONTEXT_VARS, blank_variables
from gosdoc.letters.models import Letter, LetterBlank, LetterVersion

router = APIRouter(prefix="/api/letters", tags=["letters"])
blanks_router = APIRouter(prefix="/api/letter-blanks", tags=["letters"])


def get_llm() -> LLMClient:
    return LLMClient(get_settings())


class Body(BaseModel):
    title: str = Field(default="", max_length=500)
    greeting: str = Field(default="", max_length=300)
    paragraphs: list[str] = Field(default_factory=list, max_length=100)
    closing: str = Field(default="", max_length=5000)


class Requisites(BaseModel):
    incoming_number: str = Field(default="", max_length=100)
    incoming_date: dt.date | None = None  # для 59-ФЗ — дата регистрации обращения
    extended: bool = False  # срок ответа продлён (ч. 2 ст. 12 59-ФЗ)
    attachments: list[str] = Field(default_factory=list, max_length=50)
    signer_position: str = Field(default="", max_length=300)
    signer_name: str = Field(default="", max_length=200)
    executor_phone: str = Field(default="", max_length=100)


class LetterCreate(BaseModel):
    type: str
    description: str = Field(default="", max_length=5000)
    incoming_file_id: int | None = None
    blank_id: int | None = None
    addressee: str = Field(default="", max_length=2000)
    executor: str = Field(default="", max_length=300)
    requisites: Requisites = Field(default_factory=Requisites)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in service.letter_types():
            raise ValueError(f"Неизвестный тип письма: {v}")
        return v


class LetterUpdate(BaseModel):
    body: Body
    addressee: str = Field(default="", max_length=2000)
    executor: str = Field(default="", max_length=300)
    outgoing_number: str = Field(default="", max_length=100)
    date: dt.date | None = None
    blank_id: int | None = None
    requisites: Requisites = Field(default_factory=Requisites)


class VersionOut(BaseModel):
    id: int
    version: int
    body: dict[str, Any]
    author_id: int | None
    docx_file_id: int | None
    pdf_file_id: int | None
    meta: dict[str, Any]
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class LetterOut(BaseModel):
    id: int
    type: str
    blank_id: int | None
    incoming_file_id: int | None
    outgoing_number: str | None
    date: dt.date | None
    addressee: str
    executor: str
    deadline: dt.date | None
    status: str
    is_draft: bool
    requisites: dict[str, Any]
    created_by: int | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class LetterDetail(LetterOut):
    versions: list[VersionOut]
    checks: dict[str, Any]


class BlankOut(BaseModel):
    id: int
    name: str
    template_file_id: int
    fields: dict[str, Any]
    active: bool

    model_config = {"from_attributes": True}


class BlankIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    template_file_id: int


# --- Вспомогательное ------------------------------------------------------------


def _letter(db: Session, user: User, letter_id: int) -> Letter:
    letter = OrgRepository(Letter, db, user.organization_id).get(letter_id)
    if letter is None or not can_view(db, user, letter.created_by):
        raise HTTPException(404, "Письмо не найдено")
    return letter


def _editable(db: Session, user: User, letter_id: int) -> Letter:
    letter = _letter(db, user, letter_id)
    if letter.status == "approved":
        raise HTTPException(409, "Письмо утверждено, изменения запрещены")
    return letter


def _versions(db: Session, letter_id: int) -> list[LetterVersion]:
    stmt = (
        select(LetterVersion)
        .where(LetterVersion.letter_id == letter_id)
        .order_by(LetterVersion.version)
    )
    return list(db.scalars(stmt))


def _check_blank(db: Session, user: User, blank_id: int | None) -> None:
    if blank_id is None:
        return
    blank = OrgRepository(LetterBlank, db, user.organization_id).get(blank_id)
    if blank is None or not blank.active:
        raise HTTPException(404, "Бланк не найден")


def _detail(db: Session, letter: Letter) -> LetterDetail:
    versions = _versions(db, letter.id)
    checks = service.run_checks(
        letter, versions[-1].body, service.org_settings(db, letter.organization_id)
    )
    return LetterDetail(
        **LetterOut.model_validate(letter).model_dump(),
        versions=[VersionOut.model_validate(v) for v in versions],
        checks={
            "deadline": checks.deadline,
            "days_left": checks.days_left,
            "number_ok": checks.number_ok,
            "style": checks.style,
        },
    )


# --- Письма ----------------------------------------------------------------------


@router.get("/types")
def types(_: User = Depends(current_user)) -> list[dict[str, str]]:
    return [{"id": k, "name": v["name"]} for k, v in service.letter_types().items()]


@router.post("", response_model=LetterDetail, status_code=201)
def create_letter(
    body: LetterCreate,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
    llm: LLMClient = Depends(get_llm),
) -> LetterDetail:
    _check_blank(db, user, body.blank_id)
    incoming = ""
    if body.incoming_file_id is not None:
        f = readable_file(db, user, body.incoming_file_id)
        try:
            incoming = extract_text(storage.get(f.storage_key), f.original_name)
        except TextExtractionError as e:
            raise HTTPException(400, str(e)) from e
    requisites = body.requisites.model_dump(mode="json")
    letter = OrgRepository(Letter, db, user.organization_id).add(
        Letter(
            type=body.type,
            blank_id=body.blank_id,
            incoming_file_id=body.incoming_file_id,
            addressee=body.addressee,
            executor=body.executor or user.full_name,
            requisites=requisites,
            deadline=service.compute_deadline(db, body.type, requisites),
            status="draft",
            is_draft=True,
            created_by=user.id,
        )
    )
    draft, meta = service.draft_body(llm, body.type, body.description, incoming)
    db.add(LetterVersion(letter_id=letter.id, version=1, body=draft, author_id=None, meta=meta))
    db.flush()
    audit_request(
        db,
        request,
        user,
        "letters.created",
        object_type="letter",
        object_id=letter.id,
        details={"type": body.type, **meta},
    )
    return _detail(db, letter)


@router.get("", response_model=list[LetterOut])
def list_letters(
    db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> list[Letter]:
    letters = OrgRepository(Letter, db, user.organization_id).list(
        order_by=Letter.id.desc(), limit=500
    )
    return [x for x in letters if can_view(db, user, x.created_by)][:200]


@router.get("/{letter_id}", response_model=LetterDetail)
def get_letter(
    letter_id: int,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
) -> LetterDetail:
    return _detail(db, _letter(db, user, letter_id))


@router.put("/{letter_id}", response_model=LetterDetail)
def update_letter(
    letter_id: int,
    body: LetterUpdate,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
) -> LetterDetail:
    """Каждое сохранение — новая версия текста; реквизиты обновляются в письме."""
    letter = _editable(db, user, letter_id)
    _check_blank(db, user, body.blank_id)
    requisites = body.requisites.model_dump(mode="json")
    letter.addressee, letter.executor = body.addressee, body.executor
    letter.outgoing_number = body.outgoing_number.strip() or None
    letter.date, letter.blank_id, letter.requisites = body.date, body.blank_id, requisites
    letter.deadline = service.compute_deadline(db, letter.type, requisites)
    last = db.scalar(
        select(func.max(LetterVersion.version)).where(LetterVersion.letter_id == letter.id)
    )
    db.add(
        LetterVersion(
            letter_id=letter.id,
            version=(last or 0) + 1,
            body=body.body.model_dump(),
            author_id=user.id,
            meta={},
        )
    )
    db.flush()
    audit_request(db, request, user, "letters.saved", object_type="letter", object_id=letter.id)
    return _detail(db, letter)


@router.post("/{letter_id}/docx", response_model=VersionOut)
def make_docx(
    letter_id: int,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
) -> LetterVersion:
    letter = _letter(db, user, letter_id)
    version = _versions(db, letter.id)[-1]
    service.save_docx(db, storage, get_settings(), letter, version)
    audit_request(
        db, request, user, "letters.docx_created", object_type="letter", object_id=letter.id
    )
    return version


@router.post("/{letter_id}/pdf", status_code=202)
def make_pdf(
    letter_id: int,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(current_user),
    enqueue: Enqueue = Depends(get_enqueue),
) -> dict[str, int]:
    """PDF собирает воркер (LibreOffice); результат появится в версии письма."""
    letter = _letter(db, user, letter_id)
    version = _versions(db, letter.id)[-1]
    job = OrgRepository(Job, db, user.organization_id).add(
        Job(
            kind=service.PDF_JOB,
            status="queued",
            params={"version_id": version.id},
            result={},
            created_by=user.id,
        )
    )
    audit_request(
        db, request, user, "letters.pdf_requested", object_type="letter", object_id=letter.id
    )
    enqueue_after_commit(db, enqueue, "letters", "process_letter_pdf", [job.id])
    return {"job_id": job.id, "version": version.version}


@router.get("/jobs/{job_id}")
def pdf_job(
    job_id: int, db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> dict[str, Any]:
    job = OrgRepository(Job, db, user.organization_id).get(job_id)
    if job is None or job.kind != service.PDF_JOB or job.created_by != user.id:
        raise HTTPException(404, "Задача не найдена")
    return {"id": job.id, "status": job.status, "result": job.result, "error": job.error}


@router.post("/{letter_id}/approve", response_model=LetterDetail)
def approve(
    letter_id: int,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    user: User = Depends(require_roles("manager")),
) -> LetterDetail:
    """Руководитель снимает отметку «черновик» после проверки письма."""
    letter = _editable(db, user, letter_id)
    if not letter.outgoing_number or not letter.date:
        raise HTTPException(400, "Перед утверждением укажите исходящий номер и дату")
    if not service.number_ok(
        letter.outgoing_number, service.org_settings(db, letter.organization_id)
    ):
        raise HTTPException(400, "Исходящий номер не соответствует маске организации")
    letter.status, letter.is_draft = "approved", False
    audit_request(db, request, user, "letters.approved", object_type="letter", object_id=letter.id)
    return _detail(db, letter)


# --- Бланки ----------------------------------------------------------------------


@blanks_router.get("", response_model=list[BlankOut])
def list_blanks(
    db: Session = Depends(get_db, scope="function"), user: User = Depends(current_user)
) -> list[LetterBlank]:
    return OrgRepository(LetterBlank, db, user.organization_id).list(LetterBlank.active.is_(True))


@blanks_router.post("", response_model=BlankOut, status_code=201)
def create_blank(
    body: BlankIn,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    admin: User = Depends(require_roles("sys_admin")),
    storage: FileStorage = Depends(get_storage),
) -> LetterBlank:
    f = readable_file(db, admin, body.template_file_id)
    if not f.original_name.lower().endswith(".docx"):
        raise HTTPException(400, "Бланк загружается в формате DOCX")
    try:
        variables = blank_variables(storage.get(f.storage_key))
    except Exception as e:  # повреждённый DOCX или ошибка синтаксиса шаблона
        raise HTTPException(400, f"Шаблон бланка не читается: {type(e).__name__}") from e
    unknown = variables - set(CONTEXT_VARS)
    if unknown:
        raise HTTPException(400, f"Неизвестные переменные бланка: {', '.join(sorted(unknown))}")
    # Бланк хранится бессрочно: в нём нет персональных данных.
    f.contains_pdn, f.expires_at = False, None
    blank = OrgRepository(LetterBlank, db, admin.organization_id).add(
        LetterBlank(
            name=body.name,
            template_file_id=f.id,
            fields={"variables": sorted(variables)},
            uploaded_by=admin.id,
            active=True,
        )
    )
    audit_request(
        db, request, admin, "letters.blank_uploaded", object_type="blank", object_id=blank.id
    )
    return blank


@blanks_router.delete("/{blank_id}", status_code=204)
def deactivate_blank(
    blank_id: int,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    admin: User = Depends(require_roles("sys_admin")),
) -> None:
    blank = OrgRepository(LetterBlank, db, admin.organization_id).get(blank_id)
    if blank is None:
        raise HTTPException(404, "Бланк не найден")
    blank.active = False  # письма со ссылкой на бланк остаются воспроизводимыми
    audit_request(
        db, request, admin, "letters.blank_disabled", object_type="blank", object_id=blank.id
    )
