"""Общие зависимости FastAPI: текущий пользователь, роли, контекст запроса."""

from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from gosdoc.core.audit import audit
from gosdoc.core.auth import AuthError, SessionStore, check_password_policy, get_session_store
from gosdoc.core.config import get_settings
from gosdoc.core.db import User, get_db

COOKIE = "gosdoc_session"


def client_ip(request: Request) -> str | None:
    """Адрес клиента; за nginx берётся из X-Forwarded-For (uvicorn --proxy-headers)."""
    return request.client.host if request.client else None


def audit_request(db: Session, request: Request, user: User, action: str, **fields: Any) -> None:
    """Запись аудита о действии пользователя в рамках HTTP-запроса."""
    audit(
        db,
        organization_id=user.organization_id,
        user_id=user.id,
        ip=client_ip(request),
        action=action,
        **fields,
    )


def check_new_password(password: str) -> None:
    try:
        check_password_policy(password, get_settings())
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


def current_user_allow_password_change(
    request: Request,
    db: Session = Depends(get_db),
    sessions: SessionStore = Depends(get_session_store),
) -> User:
    """Пользователь сессии; допускает тех, кто ещё должен сменить пароль."""
    token = request.cookies.get(COOKIE)
    data = sessions.get(token) if token else None
    user = db.get(User, data.user_id) if data else None
    # Сессия, выданная в другой организации (пользователя перенесли), недействительна.
    if data is None or user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход в систему")
    if user.organization_id != data.organization_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход в систему")
    return user


def current_user(user: User = Depends(current_user_allow_password_change)) -> User:
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Необходимо сменить пароль")
    return user


def require_roles(*roles: str) -> Callable[[User], User]:
    def dep(user: User = Depends(current_user)) -> User:
        if not set(roles) & set(user.roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user

    return dep
