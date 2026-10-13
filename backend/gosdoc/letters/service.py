"""Логика писем: черновик текста от модели, реквизиты, проверки, DOCX и PDF."""

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gosdoc.core.audit import audit
from gosdoc.core.config import Settings
from gosdoc.core.db import Job, Organization, StoredFile
from gosdoc.core.files import save_file
from gosdoc.core.llm import LLMClient
from gosdoc.core.storage import FileStorage
from gosdoc.letters.deadlines import Calendar, citizen_appeal_deadline
from gosdoc.letters.gost import LetterDoc, build_docx, render_blank
from gosdoc.letters.models import Holiday, Letter, LetterBlank, LetterVersion
from gosdoc.letters.style_check import check_style

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_JOB = "letter_pdf"
MAX_INCOMING_CHARS = 12000  # примерно треть контекста модели 8k токенов
BODY_KEYS = ("title", "greeting", "paragraphs", "closing")


@cache
def letter_types() -> dict[str, dict[str, Any]]:
    """Тип письма → конфигурация из prompts/<тип>.yaml."""
    return {p.stem: yaml.safe_load(p.read_text("utf-8")) for p in sorted(PROMPTS.glob("*.yaml"))}


def _clean_body(raw: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    """Только ожидаемые поля и типы; чего нет — из шаблона структуры."""
    raw = raw if isinstance(raw, dict) else {}
    body: dict[str, Any] = {}
    for key in ("title", "greeting", "closing"):
        value = raw.get(key)
        body[key] = value.strip() if isinstance(value, str) else fallback.get(key, "")
    paragraphs = raw.get("paragraphs")
    if isinstance(paragraphs, list) and all(isinstance(p, str) for p in paragraphs) and paragraphs:
        body["paragraphs"] = [p.strip() for p in paragraphs if p.strip()]
    else:
        body["paragraphs"] = list(fallback.get("paragraphs", []))
    return body


def draft_body(
    llm: LLMClient, letter_type: str, description: str, incoming: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Смысловые блоки письма от модели. При сбое — шаблон структуры с пометками."""
    cfg = letter_types()[letter_type]
    meta: dict[str, Any] = {"prompt_version": cfg["version"], "model_name": llm.s.llm_model}
    user = cfg["user"].format(
        description=description.strip() or "—",
        incoming=incoming[:MAX_INCOMING_CHARS].strip() or "—",
    )
    try:
        answer = llm.chat(
            [{"role": "system", "content": cfg["system"]}, {"role": "user", "content": user}]
        )
        raw = json.loads(answer[answer.find("{") : answer.rfind("}") + 1] or "{}")
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("Модель не подготовила черновик письма: %s", type(e).__name__)
        raw, meta["fallback"] = {}, True
    body = _clean_body(raw, cfg["skeleton"])
    if body == _clean_body({}, cfg["skeleton"]):
        meta["fallback"] = True
    return body, meta


# --- Реквизиты и проверки --------------------------------------------------------


def org_settings(db: Session, organization_id: int) -> dict[str, Any]:
    org = db.get(Organization, organization_id)
    return dict(org.settings or {}) if org else {}


def number_ok(number: str, settings: dict[str, Any]) -> bool:
    pattern = settings.get("outgoing_number_pattern") or r".+"
    return re.fullmatch(pattern, number.strip()) is not None


def calendar(db: Session) -> Calendar:
    return Calendar({h.date: h.is_workday for h in db.scalars(select(Holiday))})


def compute_deadline(db: Session, letter_type: str, requisites: dict[str, Any]) -> date | None:
    if letter_types()[letter_type]["deadline"] != "citizen_appeal":
        return None
    registered = requisites.get("incoming_date")
    if not registered:
        return None
    return citizen_appeal_deadline(
        date.fromisoformat(registered), calendar(db), bool(requisites.get("extended"))
    )


@dataclass(frozen=True)
class Checks:
    deadline: date | None
    days_left: int | None
    number_ok: bool | None
    style: list[dict[str, Any]]


def run_checks(letter: Letter, body: dict[str, Any], settings: dict[str, Any]) -> Checks:
    texts = [
        body.get("title", ""),
        body.get("greeting", ""),
        *body.get("paragraphs", []),
        body.get("closing", ""),
    ]
    style = [
        {"paragraph": i.paragraph, "fragment": i.fragment, "message": i.message}
        for i in check_style(texts)
        # Восклицательный знак допустим в обращении (абзац 1).
        if not (i.paragraph == 1 and i.fragment == "!")
    ]
    days_left = (letter.deadline - date.today()).days if letter.deadline else None
    number = letter.outgoing_number
    return Checks(
        letter.deadline, days_left, number_ok(number, settings) if number else None, style
    )


def to_doc(letter: Letter, body: dict[str, Any], settings: dict[str, Any]) -> LetterDoc:
    req = letter.requisites
    blank = settings.get("blank", {})
    return LetterDoc(
        org_name=blank.get("org_name", ""),
        org_details=list(blank.get("details", [])),
        date=letter.date,
        number=letter.outgoing_number or "",
        incoming_number=req.get("incoming_number", ""),
        incoming_date=date.fromisoformat(req["incoming_date"])
        if req.get("incoming_date")
        else None,
        addressee=[line for line in letter.addressee.splitlines() if line.strip()],
        title=body.get("title", ""),
        greeting=body.get("greeting", ""),
        paragraphs=list(body.get("paragraphs", [])),
        closing=body.get("closing", ""),
        attachments=list(req.get("attachments", [])),
        signer_position=req.get("signer_position") or settings.get("signer_position", ""),
        signer_name=req.get("signer_name") or settings.get("signer_name", ""),
        executor=letter.executor,
        executor_phone=req.get("executor_phone", ""),
    )


def render_docx(db: Session, storage: FileStorage, letter: Letter, version: LetterVersion) -> bytes:
    doc = to_doc(letter, version.body, org_settings(db, letter.organization_id))
    blank = db.get(LetterBlank, letter.blank_id) if letter.blank_id else None
    if blank is None or blank.organization_id != letter.organization_id:
        return build_docx(doc)
    template = db.get(StoredFile, blank.template_file_id)
    if template is None:
        return build_docx(doc)
    return render_blank(storage.get(template.storage_key), doc)


def file_stem(letter: Letter, version: LetterVersion) -> str:
    return f"Письмо {letter.outgoing_number or letter.id} v{version.version}".replace("/", "-")


def save_docx(
    db: Session, storage: FileStorage, s: Settings, letter: Letter, version: LetterVersion
) -> StoredFile:
    f = save_file(
        db,
        storage,
        s,
        organization_id=letter.organization_id,
        owner_id=letter.created_by,
        name=f"{file_stem(letter, version)}.docx",
        mime=DOCX_MIME,
        data=render_docx(db, storage, letter, version),
    )
    version.docx_file_id = f.id
    return f


# --- PDF (воркер) -----------------------------------------------------------------


class ConversionError(RuntimeError):
    pass


def docx_to_pdf(data: bytes, timeout_s: int = 120) -> bytes:
    """LibreOffice headless; отдельный профиль на вызов — параллельные запуски не мешают."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "letter.docx"
        src.write_bytes(data)
        cmd = [
            "soffice",
            f"-env:UserInstallation=file://{tmp}/profile",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            tmp,
            str(src),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout_s)  # noqa: S603
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            raise ConversionError(f"LibreOffice: {type(e).__name__}") from e
        pdf = Path(tmp) / "letter.pdf"
        if not pdf.exists():
            raise ConversionError("LibreOffice не создал PDF")
        return pdf.read_bytes()


def run_pdf_job(
    sessions: sessionmaker[Session], storage: FileStorage, s: Settings, job_id: int
) -> None:
    with sessions.begin() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != "queued":
            return
        job.status, job.started_at = "running", datetime.now(UTC)
    try:
        with sessions.begin() as db:
            job = db.get(Job, job_id)
            version = db.get(LetterVersion, job.params["version_id"]) if job else None
            letter = db.get(Letter, version.letter_id) if version else None
            if job is None or version is None or letter is None:
                raise ConversionError("Письмо удалено")
            pdf = docx_to_pdf(render_docx(db, storage, letter, version))
            f = save_file(
                db,
                storage,
                s,
                organization_id=letter.organization_id,
                owner_id=letter.created_by,
                name=f"{file_stem(letter, version)}.pdf",
                mime="application/pdf",
                data=pdf,
            )
            version.pdf_file_id = f.id
            job.status, job.finished_at, job.result = (
                "done",
                datetime.now(UTC),
                {"pdf_file_id": f.id},
            )
            audit(
                db,
                organization_id=letter.organization_id,
                user_id=job.created_by,
                action="letters.pdf_created",
                object_type="letter",
                object_id=letter.id,
            )
    except Exception as e:
        log.exception("Сбой конвертации письма в PDF, задача %s", job_id)
        message = str(e) if isinstance(e, ConversionError) else "Внутренняя ошибка конвертации"
        with sessions.begin() as db:
            failed = db.get(Job, job_id)
            if failed is not None:
                failed.status, failed.error, failed.finished_at = (
                    "failed",
                    message,
                    datetime.now(UTC),
                )
