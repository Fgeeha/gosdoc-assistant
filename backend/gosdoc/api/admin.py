"""Администрирование: пользователи и журнал аудита (роль sys_admin)."""

import re
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from gosdoc.api.deps import audit_request, check_new_password, require_roles
from gosdoc.core.auth import (
    SessionStore,
    get_session_store,
    hash_password,
    new_local_user,
    validate_roles,
)
from gosdoc.core.db import AuditLog, Organization, User, get_db
from gosdoc.core.repository import OrgRepository
from gosdoc.letters.models import Holiday

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_only = require_roles("sys_admin")


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
    department: str | None = Field(None, max_length=300)
    roles: list[str] = ["user"]
    password: str  # временный, при первом входе пользователь его сменит

    _roles = field_validator("roles")(validate_roles)


class UserPatch(BaseModel):
    # Не переданное поле не меняется; full_name, roles и is_active нельзя обнулить.
    full_name: str | None = Field(None, min_length=1, max_length=300)
    department: str | None = Field(None, max_length=300)
    roles: list[str] | None = None
    is_active: bool | None = None
    unlock: bool = False
    new_password: str | None = None

    @field_validator("full_name", "roles", "is_active")
    @classmethod
    def _not_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("Поле не может быть пустым")
        return v

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: list[str]) -> list[str]:
        return validate_roles(v)


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
def list_users(
    db: Session = Depends(get_db, scope="function"), admin: User = Depends(admin_only)
) -> list[User]:
    return OrgRepository(User, db, admin.organization_id).list(order_by=User.login, limit=1000)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    admin: User = Depends(admin_only),
) -> User:
    # Логин уникален во всей системе, а не в организации, поэтому проверка без
    # фильтра по организации (ADR-0006): раскрывается только факт занятости логина.
    if db.scalar(select(User.id).where(User.login == body.login)):
        raise HTTPException(409, "Пользователь с таким логином уже есть")
    check_new_password(body.password)
    user = OrgRepository(User, db, admin.organization_id).add(
        new_local_user(
            organization_id=admin.organization_id,
            login=body.login,
            full_name=body.full_name,
            department=body.department,
            roles=body.roles,
            password=body.password,
        )
    )
    audit_request(
        db,
        request,
        admin,
        "user.created",
        object_type="user",
        object_id=user.id,
        details={"login": user.login, "roles": user.roles},
    )
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: int,
    body: UserPatch,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    sessions: SessionStore = Depends(get_session_store),
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
        check_new_password(body.new_password)
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = True
    if body.new_password or body.is_active is False or body.roles is not None:
        sessions.revoke_user(user.id)
    audit_request(
        db,
        request,
        admin,
        "user.updated",
        object_type="user",
        object_id=user.id,
        details={**changes, "unlock": body.unlock, "password_reset": bool(body.new_password)},
    )
    return user


@router.get("/audit", response_model=list[AuditOut])
def list_audit(
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    db: Session = Depends(get_db, scope="function"),
    admin: User = Depends(admin_only),
) -> list[AuditLog]:
    criteria = [AuditLog.action == action] if action else []
    return OrgRepository(AuditLog, db, admin.organization_id).list(
        *criteria, order_by=AuditLog.id.desc(), limit=min(limit, 1000), offset=offset
    )


# --- Настройки организации и производственный календарь -------------------------


class BlankSettings(BaseModel):
    org_name: str = Field(default="", max_length=500)
    details: list[str] = Field(default_factory=list, max_length=10)  # адрес, телефон, e-mail


class OrgSettings(BaseModel):
    blank: BlankSettings = Field(default_factory=BlankSettings)
    # Маска исходящего номера — регулярное выражение, например ^\d{2}-\d{2}/\d+$.
    outgoing_number_pattern: str = Field(default="", max_length=200)
    outgoing_number_example: str = Field(default="", max_length=100)
    signer_position: str = Field(default="", max_length=300)
    signer_name: str = Field(default="", max_length=200)

    @field_validator("outgoing_number_pattern")
    @classmethod
    def _regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Маска номера — некорректное регулярное выражение: {e}") from e
        return v


class OrgOut(BaseModel):
    id: int
    name: str
    short_name: str
    settings: OrgSettings


class HolidayIn(BaseModel):
    date: date
    is_workday: bool = False
    note: str = Field(default="", max_length=200)


class HolidayOut(HolidayIn):
    model_config = {"from_attributes": True}


def _org(db: Session, admin: User) -> Organization:
    org = db.get(Organization, admin.organization_id)
    if org is None:
        raise HTTPException(404, "Организация не найдена")
    return org


@router.get("/organization", response_model=OrgOut)
def get_organization(
    db: Session = Depends(get_db, scope="function"), admin: User = Depends(admin_only)
) -> OrgOut:
    org = _org(db, admin)
    return OrgOut(
        id=org.id,
        name=org.name,
        short_name=org.short_name,
        settings=OrgSettings.model_validate(org.settings or {}),
    )


@router.put("/organization", response_model=OrgOut)
def update_organization(
    body: OrgSettings,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    admin: User = Depends(admin_only),
) -> OrgOut:
    org = _org(db, admin)
    org.settings = {**(org.settings or {}), **body.model_dump()}
    audit_request(
        db,
        request,
        admin,
        "organization.settings_updated",
        object_type="organization",
        object_id=org.id,
    )
    return OrgOut(id=org.id, name=org.name, short_name=org.short_name, settings=body)


@router.get("/holidays", response_model=list[HolidayOut])
def list_holidays(
    year: int | None = None,
    db: Session = Depends(get_db, scope="function"),
    _: User = Depends(admin_only),
) -> list[Holiday]:
    stmt = select(Holiday).order_by(Holiday.date)
    if year:
        stmt = stmt.where(Holiday.date >= date(year, 1, 1), Holiday.date <= date(year, 12, 31))
    return list(db.scalars(stmt))


@router.put("/holidays", response_model=HolidayOut)
def set_holiday(
    body: HolidayIn,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    admin: User = Depends(admin_only),
) -> Holiday:
    """Отклонение от календаря по умолчанию (перенос выходного, рабочая суббота)."""
    h = db.merge(Holiday(date=body.date, is_workday=body.is_workday, note=body.note))
    audit_request(db, request, admin, "calendar.updated", details=body.model_dump(mode="json"))
    return h


@router.delete("/holidays/{day}", status_code=204)
def delete_holiday(
    day: date,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    admin: User = Depends(admin_only),
) -> None:
    h = db.get(Holiday, day)
    if h is not None:
        db.delete(h)
        audit_request(
            db,
            request,
            admin,
            "calendar.updated",
            details={"date": day.isoformat(), "removed": True},
        )
