"""Нормативная база: загрузка актов и редакций, индексация, журнал изменений."""

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from gosdoc.core.config import Settings
from gosdoc.core.db import StoredFile
from gosdoc.core.llm import LLMClient
from gosdoc.core.storage import FileStorage
from gosdoc.core.text import extract_paragraphs
from gosdoc.docs_check.models import NpaAct, NpaChangeLog, NpaChunk, NpaEdition
from gosdoc.docs_check.npa_refs import KINDS, ActHeader, canonical_key
from rag.chunker import chunk_act
from rag.search import ChromaIndex, ElasticIndex, IndexedChunk, MilvusIndex, TextIndex, VectorIndex

log = logging.getLogger(__name__)
EMBED_BATCH = 32


@dataclass(frozen=True)
class SearchBackends:
    text: TextIndex
    vectors: VectorIndex
    llm: LLMClient


def make_backends(s: Settings) -> SearchBackends:
    verify: Any = s.ca_bundle or True
    vectors: VectorIndex = (
        MilvusIndex(s.vector_store_url, verify=verify)
        if s.vector_store == "milvus"
        else ChromaIndex(s.vector_store_url, verify=verify)
    )
    return SearchBackends(ElasticIndex(s.elasticsearch_url, verify=verify), vectors, LLMClient(s))


def act_label(act: NpaAct) -> str:
    kind = KINDS.get(act.kind, act.kind)
    adopted = f" от {act.adopted_at:%d.%m.%Y}" if act.adopted_at else ""
    title = f" «{act.title}»" if act.title else ""
    return f"{kind}{adopted} № {act.number}{title}"


def visible_acts(organization_id: int) -> Any:
    return or_(NpaAct.organization_id.is_(None), NpaAct.organization_id == organization_id)


def find_act(db: Session, organization_id: int, key: str) -> NpaAct | None:
    """Ведомственный акт организации важнее общего с тем же ключом."""
    acts = db.scalars(
        select(NpaAct).where(NpaAct.canonical_key == key, visible_acts(organization_id))
    )
    return max(acts, key=lambda a: a.organization_id is not None, default=None)


def edition_at(db: Session, act_id: int, when: date) -> NpaEdition | None:
    """Редакция, действовавшая на дату (последняя с effective_from ≤ when)."""
    stmt = (
        select(NpaEdition)
        .where(NpaEdition.act_id == act_id, NpaEdition.effective_from <= when)
        .order_by(NpaEdition.effective_from.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def log_change(db: Session, act: NpaAct, change: str, user_id: int | None, **details: Any) -> None:
    db.add(NpaChangeLog(act_id=act.id, change=change, user_id=user_id, details=details))


@dataclass
class UploadResult:
    act: NpaAct
    edition: NpaEdition
    created_act: bool


def add_edition(
    db: Session,
    *,
    file: StoredFile,
    paragraphs: Sequence[str],
    header: ActHeader,
    organization_id: int | None,
    user_id: int | None,
    effective_from: date | None = None,
    title: str = "",
) -> UploadResult:
    """Акт создаётся по ключу при первой загрузке, далее каждая загрузка — новая редакция."""
    key = canonical_key(header.kind, header.number, header.adopted_at)
    act = db.scalar(
        select(NpaAct).where(
            NpaAct.canonical_key == key,
            NpaAct.organization_id.is_(None)
            if organization_id is None
            else NpaAct.organization_id == organization_id,
        )
    )
    created = act is None
    if act is None:
        act = NpaAct(
            organization_id=organization_id,
            kind=header.kind,
            number=header.number,
            adopted_at=header.adopted_at,
            title=title or header.title,
            canonical_key=key,
        )
        db.add(act)
        db.flush()
        log_change(db, act, "added", user_id, label=act_label(act))
    start = effective_from or header.edition_date or header.adopted_at or date.today()
    sha = hashlib.sha256("\n".join(paragraphs).encode()).hexdigest()
    existing = db.scalar(
        select(NpaEdition).where(NpaEdition.act_id == act.id, NpaEdition.effective_from == start)
    )
    if existing is not None:  # повторная загрузка той же редакции — переиндексация
        existing.source_file_id, existing.text_sha256, existing.status = file.id, sha, "queued"
        return UploadResult(act, existing, created)
    edition = NpaEdition(
        act_id=act.id,
        effective_from=start,
        source_file_id=file.id,
        text_sha256=sha,
        status="queued",
    )
    db.add(edition)
    db.flush()
    # Границы действия редакций пересчитываются по порядку дат.
    editions = list(
        db.scalars(
            select(NpaEdition)
            .where(NpaEdition.act_id == act.id)
            .order_by(NpaEdition.effective_from)
        )
    )
    for prev, nxt in pairwise(editions):
        prev.effective_to = nxt.effective_from - timedelta(days=1)
    editions[-1].effective_to = None
    if not created:
        log_change(db, act, "new_edition", user_id, effective_from=start.isoformat())
    return UploadResult(act, edition, created)


def index_edition(
    sessions: sessionmaker[Session],
    storage: FileStorage,
    backends: SearchBackends,
    s: Settings,
    edition_id: int,
) -> int:
    """Разбивка редакции на фрагменты и запись в PostgreSQL, Elasticsearch и векторный индекс."""
    with sessions.begin() as db:
        edition = db.get(NpaEdition, edition_id)
        act = db.get(NpaAct, edition.act_id) if edition else None
        source = (
            db.get(StoredFile, edition.source_file_id)
            if edition and edition.source_file_id
            else None
        )
        if edition is None or act is None or source is None:
            raise ValueError(f"Редакция {edition_id} или её файл не найдены")
        paragraphs = extract_paragraphs(
            storage.get(source.storage_key), source.original_name, ocr=True
        )
        # Размер фрагмента — с запасом под несколько фрагментов в контексте модели.
        max_chars = max(500, s.llm_max_context // 4)
        db.execute(delete(NpaChunk).where(NpaChunk.edition_id == edition_id))
        rows = [
            NpaChunk(
                edition_id=edition_id,
                path=c.path,
                article=c.article,
                heading=c.heading,
                text=c.text,
            )
            for c in chunk_act("\n".join(paragraphs), max_chars)
        ]
        db.add_all(rows)
        db.flush()
        chunks = [
            IndexedChunk(str(r.id), r.text, act.id, edition_id, act.organization_id, r.path)
            for r in rows
        ]

    backends.text.ensure()
    backends.text.delete_edition(edition_id)
    backends.text.add(chunks)
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        vectors = backends.llm.embed([c.text for c in batch])
        if i == 0:
            backends.vectors.ensure(len(vectors[0]))
            backends.vectors.delete_edition(edition_id)
        backends.vectors.add(batch, vectors)

    with sessions.begin() as db:
        edition = db.get(NpaEdition, edition_id)
        if edition is not None:
            edition.status, edition.error, edition.indexed_at = "indexed", None, datetime.now(UTC)
    log.info("Редакция %s проиндексирована: %d фрагментов", edition_id, len(chunks))
    return len(chunks)


def mark_failed(sessions: sessionmaker[Session], edition_id: int, message: str) -> None:
    with sessions.begin() as db:
        edition = db.get(NpaEdition, edition_id)
        if edition is not None:
            edition.status, edition.error = "failed", message[:2000]
