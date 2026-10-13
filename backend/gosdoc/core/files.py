"""Файлы пользователей: сохранение с метаданными и автоудаление по сроку хранения."""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from gosdoc.core.audit import audit
from gosdoc.core.config import Settings
from gosdoc.core.db import StoredFile
from gosdoc.core.storage import FileStorage

log = logging.getLogger(__name__)


class FileTooLarge(ValueError):
    pass


def save_file(
    db: Session,
    storage: FileStorage,
    s: Settings,
    *,
    organization_id: int,
    owner_id: int | None,
    name: str,
    mime: str,
    data: bytes,
    contains_pdn: bool = True,
) -> StoredFile:
    """Пишет метаданные в `files` и байты в хранилище. Файлы с ПДн получают срок хранения.

    Если транзакция `db` затем откатится, содержимое удаляется из хранилища:
    иначе остался бы файл с ПДн без записи, который не найдёт автоудаление.
    """
    if len(data) > s.storage_max_file_mb * 1024 * 1024:
        raise FileTooLarge(f"Файл больше {s.storage_max_file_mb} МБ")
    key = f"{organization_id}/{uuid.uuid4().hex}"
    expires = datetime.now(UTC) + timedelta(days=s.file_retention_days) if contains_pdn else None
    f = StoredFile(
        organization_id=organization_id,
        owner_id=owner_id,
        storage_key=key,
        original_name=name[:500],
        mime=mime or "application/octet-stream",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        contains_pdn=contains_pdn,
        expires_at=expires,
    )
    db.add(f)
    db.flush()
    storage.put(key, data)
    _delete_on_rollback(db, storage, key)
    return f


def _delete_on_rollback(db: Session, storage: FileStorage, key: str) -> None:
    committed = False

    def on_commit(_: Session) -> None:
        nonlocal committed
        committed = True

    def on_rollback(_: Session) -> None:
        if committed:
            return
        try:
            storage.delete(key)
        except Exception:  # сбой хранилища не должен маскировать исходную ошибку
            log.exception("Не удалось удалить файл после отката транзакции: %s", key)

    event.listen(db, "after_commit", on_commit, once=True)
    event.listen(db, "after_rollback", on_rollback, once=True)


def cleanup_expired_files(
    sessions: sessionmaker[Session], storage: FileStorage, now: datetime | None = None
) -> int:
    """Удаляет файлы с истёкшим сроком: сначала содержимое, потом метаданные.

    Удаление из хранилища идемпотентно, поэтому сбой между шагами просто
    повторится при следующем запуске.
    """
    now = now or datetime.now(UTC)
    deleted = 0
    with sessions() as db:
        expired = list(db.scalars(select(StoredFile).where(StoredFile.expires_at < now)))
    for f in expired:
        storage.delete(f.storage_key)
        with sessions.begin() as db:
            db.delete(db.merge(f))
            audit(
                db,
                organization_id=f.organization_id,
                action="file.expired_deleted",
                object_type="file",
                object_id=f.id,
                details={"sha256": f.sha256},
            )
        deleted += 1
    log.info("Удалено файлов с истёкшим сроком хранения: %d", deleted)
    return deleted
