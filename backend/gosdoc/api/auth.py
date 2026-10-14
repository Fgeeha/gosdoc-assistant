"""Вход, выход, смена пароля."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gosdoc.api.deps import (
    COOKIE,
    audit_request,
    check_new_password,
    client_ip,
    current_user_allow_password_change,
)
from gosdoc.core.audit import audit
from gosdoc.core.auth import (
    AuthError,
    SessionStore,
    get_auth_provider,
    get_session_store,
    hash_password,
    verify_password,
)
from gosdoc.core.config import get_settings
from gosdoc.core.db import User, get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    login: str
    password: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


class Me(BaseModel):
    id: int
    login: str
    full_name: str
    department: str | None
    roles: list[str]
    must_change_password: bool
    organization_id: int


def me_out(u: User) -> Me:
    return Me(
        id=u.id,
        login=u.login,
        full_name=u.full_name,
        department=u.department,
        roles=u.roles,
        must_change_password=u.must_change_password,
        organization_id=u.organization_id,
    )


@router.post("/login", response_model=Me)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    sessions: SessionStore = Depends(get_session_store),
) -> Me | JSONResponse:
    s = get_settings()
    try:
        user = get_auth_provider(s).authenticate(db, body.login.strip(), body.password)
    except AuthError as e:
        # Ответ, а не исключение: счётчик неудачных попыток и аудит должны закоммититься.
        audit(
            db,
            organization_id=s.default_organization_id,
            action="auth.login_failed",
            ip=client_ip(request),
            details={"login": body.login[:100], "reason": str(e)},
        )
        return JSONResponse({"detail": str(e)}, status_code=status.HTTP_401_UNAUTHORIZED)
    token = sessions.create(user)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=s.session_cookie_secure,
        max_age=s.session_ttl_minutes * 60,
    )
    audit_request(db, request, user, "auth.login")
    return me_out(user)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    sessions: SessionStore = Depends(get_session_store),
    user: User = Depends(current_user_allow_password_change),
) -> None:
    sessions.delete(request.cookies[COOKIE])
    response.delete_cookie(COOKIE)
    audit_request(db, request, user, "auth.logout")


@router.get("/me", response_model=Me)
def me(user: User = Depends(current_user_allow_password_change)) -> Me:
    return me_out(user)


@router.post("/password", status_code=204)
def change_password(
    body: PasswordIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    sessions: SessionStore = Depends(get_session_store),
    user: User = Depends(current_user_allow_password_change),
) -> None:
    if user.auth_source != "local":
        raise HTTPException(400, "Пароль меняется в службе каталогов (AD)")
    if not verify_password(user, body.old_password):
        raise HTTPException(400, "Текущий пароль указан неверно")
    if body.new_password == body.old_password:
        raise HTTPException(400, "Новый пароль должен отличаться от текущего")
    check_new_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    sessions.revoke_user(user.id)  # все сессии, включая возможно украденные
    response.delete_cookie(COOKIE)
    audit_request(db, request, user, "auth.password_changed")
