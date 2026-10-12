"""Загрузка и выгрузка файлов пользователя."""

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gosdoc.api.deps import client_ip, current_user
from gosdoc.core.audit import audit
from gosdoc.core.config import get_settings
from gosdoc.core.db import StoredFile, User, get_db
from gosdoc.core.files import FileTooLarge, save_file
from gosdoc.core.repository import OrgRepository
from gosdoc.core.storage import FileStorage, get_storage

router = APIRouter(prefix="/api/files", tags=["files"])


class FileOut(BaseModel):
    id: int
    original_name: str
    mime: str
    size: int
    sha256: str
    created_at: datetime
    expires_at: datetime | None

    model_config = {"from_attributes": True}


def _own_file(db: Session, user: User, file_id: int) -> StoredFile:
    f = OrgRepository(StoredFile, db, user.organization_id).get(file_id)
    # Чужой файл и несуществующий неразличимы для пользователя.
    if f is None or (f.owner_id != user.id and "manager" not in user.roles):
        raise HTTPException(404, "Файл не найден")
    return f


@router.post("", response_model=FileOut, status_code=201)
def upload(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
) -> StoredFile:
    s = get_settings()
    # ponytail: файл читается в память целиком; при лимите в десятки МБ это нормально.
    data = file.file.read(s.storage_max_file_mb * 1024 * 1024 + 1)
    try:
        f = save_file(
            db,
            storage,
            s,
            organization_id=user.organization_id,
            owner_id=user.id,
            name=file.filename or "file",
            mime=file.content_type or "",
            data=data,
        )
    except FileTooLarge as e:
        raise HTTPException(413, str(e)) from e
    audit(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
        action="file.uploaded",
        object_type="file",
        object_id=f.id,
        ip=client_ip(request),
        details={"name": f.original_name, "size": f.size, "sha256": f.sha256},
    )
    return f


@router.get("/{file_id}", response_model=FileOut)
def meta(
    file_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> StoredFile:
    return _own_file(db, user, file_id)


@router.get("/{file_id}/content")
def download(
    file_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    storage: FileStorage = Depends(get_storage),
) -> Response:
    f = _own_file(db, user, file_id)
    audit(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
        action="file.downloaded",
        object_type="file",
        object_id=f.id,
        ip=client_ip(request),
    )
    return Response(
        storage.get(f.storage_key),
        media_type=f.mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.original_name)}"},
    )
