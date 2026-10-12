"""Общие зависимости FastAPI: текущий пользователь, роли, контекст запроса."""

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from gosdoc.core.auth import SessionStore, get_session_store
from gosdoc.core.db import User, get_db

COOKIE = "gosdoc_session"


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_user_allow_password_change(
    request: Request,
    db: Session = Depends(get_db),
    sessions: SessionStore = Depends(get_session_store),
) -> User:
    """Пользователь сессии; допускает тех, кто ещё должен сменить пароль."""
    token = request.cookies.get(COOKIE)
    data = sessions.get(token) if token else None
    user = db.get(User, data.user_id) if data else None
    if user is None or not user.is_active:
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
