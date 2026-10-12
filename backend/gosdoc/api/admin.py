"""Администрирование: пользователи и журнал аудита (роль sys_admin)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from gosdoc.api.deps import client_ip, require_roles
from gosdoc.core.audit import audit
from gosdoc.core.auth import ROLES, AuthError, check_password_policy, hash_password
from gosdoc.core.config import get_settings
from gosdoc.core.db import AuditLog, User, get_db
from gosdoc.core.repository import OrgRepository

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_only = require_roles("sys_admin")


def _check_roles(v: list[str]) -> list[str]:
    unknown = set(v) - set(ROLES)
    if unknown:
        raise ValueError(f"Неизвестные роли: {', '.join(sorted(unknown))}")
    return sorted(set(v))


class UserOut(BaseModel):
    id: int
    login: str
    full_name: str
    department: str | None
    roles: list[str]
    auth_source: str
    is_active: bool
    locked_until: datetime | None
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    login: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    full_name: str = Field(min_length=1, max_length=300)
    department: str | None = None
    roles: list[str] = ["user"]
    password: str  # временный, при первом входе пользователь его сменит

    _roles = field_validator("roles")(_check_roles)


class UserPatch(BaseModel):
    full_name: str | None = None
    department: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None
    unlock: bool = False
    new_password: str | None = None

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _check_roles(v)


class AuditOut(BaseModel):
    id: int
    at: datetime
    user_id: int | None
    ip: str | None
    action: str
    object_type: str | None
    object_id: str | None
    details: dict[str, Any]

    model_config = {"from_attributes": True}


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), admin: User = Depends(admin_only)) -> list[User]:
    return OrgRepository(User, db, admin.organization_id).list(order_by=User.login, limit=1000)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> User:
    if db.scalar(select(User.id).where(User.login == body.login)):
        raise HTTPException(409, "Пользователь с таким логином уже есть")
    try:
        check_password_policy(body.password, get_settings())
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    user = OrgRepository(User, db, admin.organization_id).add(
        User(
            login=body.login,
            full_name=body.full_name,
            department=body.department,
            roles=body.roles,
            password_hash=hash_password(body.password),
            auth_source="local",
            must_change_password=True,
            failed_attempts=0,
            is_active=True,
        )
    )
    audit(
        db,
        organization_id=admin.organization_id,
        user_id=admin.id,
        action="user.created",
        object_type="user",
        object_id=user.id,
        ip=client_ip(request),
        details={"login": user.login, "roles": user.roles},
    )
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: int,
    body: UserPatch,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> User:
    user = OrgRepository(User, db, admin.organization_id).get(user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    changes = body.model_dump(exclude_unset=True, exclude={"new_password", "unlock"})
    if user.id == admin.id and (
        body.is_active is False or (body.roles is not None and "sys_admin" not in body.roles)
    ):
        raise HTTPException(400, "Нельзя отключить себя или снять с себя роль администратора")
    for field, value in changes.items():
        setattr(user, field, value)
    if body.unlock:
        user.locked_until, user.failed_attempts = None, 0
    if body.new_password:
        if user.auth_source != "local":
            raise HTTPException(400, "Пароль пользователя AD меняется в службе каталогов")
        try:
            check_password_policy(body.new_password, get_settings())
        except AuthError as e:
            raise HTTPException(400, str(e)) from e
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = True
    audit(
        db,
        organization_id=admin.organization_id,
        user_id=admin.id,
        action="user.updated",
        object_type="user",
        object_id=user.id,
        ip=client_ip(request),
        details={**changes, "unlock": body.unlock, "password_reset": bool(body.new_password)},
    )
    return user


@router.get("/audit", response_model=list[AuditOut])
def list_audit(
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> list[AuditLog]:
    criteria = [AuditLog.action == action] if action else []
    return OrgRepository(AuditLog, db, admin.organization_id).list(
        *criteria, order_by=AuditLog.id.desc(), limit=min(limit, 1000), offset=offset
    )
