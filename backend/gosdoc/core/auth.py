"""Аутентификация (ADR-0005): провайдеры local / ldap и сессии в Redis."""

import contextlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Protocol

import ldap3
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from ldap3.utils.conv import escape_filter_chars
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from gosdoc.core.config import Settings, get_settings
from gosdoc.core.db import User

ROLES = ("user", "manager", "npa_admin", "sys_admin")
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


class AuthError(Exception):
    """Сообщение безопасно показывать пользователю."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_password_policy(password: str, s: Settings) -> None:
    if len(password) < s.auth_min_password_length:
        raise AuthError(f"Пароль должен быть не короче {s.auth_min_password_length} символов")
    if password.isdigit() or password.isalpha():
        raise AuthError("Пароль должен содержать буквы и цифры")


def _burn_time(password: str) -> None:
    """Тратит столько же времени, сколько настоящая проверка (не даём перебирать логины)."""
    with contextlib.suppress(VerifyMismatchError):
        _hasher.verify(_DUMMY_HASH, password)


def verify_password(user: User, password: str) -> bool:
    if user.password_hash is None:
        _burn_time(password)
        return False
    try:
        _hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        return False
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    return True


class AuthProvider(Protocol):
    def authenticate(self, db: Session, login: str, password: str) -> User:
        """Возвращает пользователя или бросает AuthError. Изменения пишет в db (без commit)."""
        ...


class LocalAuthProvider:
    def __init__(self, s: Settings) -> None:
        self.s = s

    def authenticate(self, db: Session, login: str, password: str) -> User:
        user = db.scalar(select(User).where(User.login == login, User.auth_source == "local"))
        now = datetime.now(UTC)
        if user is None or not user.is_active:
            _burn_time(password)
            raise AuthError("Неверный логин или пароль")
        if user.locked_until and user.locked_until > now:
            raise AuthError("Учётная запись временно заблокирована, попробуйте позже")
        if not verify_password(user, password):
            user.failed_attempts += 1
            if user.failed_attempts >= self.s.auth_max_failed_attempts:
                user.locked_until = now + timedelta(minutes=self.s.auth_lockout_minutes)
                user.failed_attempts = 0
                raise AuthError("Слишком много неудачных попыток, учётная запись заблокирована")
            raise AuthError("Неверный логин или пароль")
        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        return user


ConnectionFactory = Callable[[str, str], ldap3.Connection]


class LdapAuthProvider:
    """Bind пользователем в AD/LDAP, роли — по группам. Пользователь заводится в users."""

    def __init__(self, s: Settings, connection_factory: ConnectionFactory | None = None) -> None:
        self.s = s
        self.connect = connection_factory or self._default_connection

    def _default_connection(self, user_dn: str, password: str) -> ldap3.Connection:
        tls = ldap3.Tls(ca_certs_file=self.s.ca_bundle) if self.s.ca_bundle else None
        server = ldap3.Server(self.s.ldap_url, tls=tls, connect_timeout=5)
        return ldap3.Connection(server, user=user_dn, password=password, read_only=True)

    def authenticate(self, db: Session, login: str, password: str) -> User:
        if not login or not password:  # пустой пароль в LDAP = анонимный bind
            raise AuthError("Неверный логин или пароль")
        conn = self.connect(self.s.ldap_user_dn_template.format(login=login), password)
        if not conn.bind():
            raise AuthError("Неверный логин или пароль")
        try:
            conn.search(
                self.s.ldap_search_base,
                self.s.ldap_user_filter.format(login=escape_filter_chars(login)),
                attributes=["displayName", "department", "memberOf"],
            )
            if not conn.entries:
                raise AuthError("Пользователь не найден в каталоге")
            entry = conn.entries[0]
        finally:
            conn.unbind()

        groups = [str(g) for g in entry.memberOf.values] if "memberOf" in entry else []
        roles = sorted(
            {
                role
                for g in groups
                for key, role in self.s.ldap_group_roles.items()
                if key.lower() == g.lower() or g.lower().startswith(f"cn={key.lower()},")
            }
        )
        if not roles:
            raise AuthError("Нет доступа к системе: учётная запись не входит в разрешённые группы")

        user = db.scalar(select(User).where(User.login == login))
        if user is None:
            user = User(
                login=login,
                organization_id=self.s.default_organization_id,
                auth_source="ldap",
                must_change_password=False,
                failed_attempts=0,
            )
            db.add(user)
        elif user.auth_source != "ldap" or not user.is_active:
            raise AuthError("Неверный логин или пароль")
        user.full_name = str(entry.displayName) if "displayName" in entry else login
        user.department = str(entry.department) if "department" in entry else None
        user.roles = roles
        user.last_login_at = datetime.now(UTC)
        db.flush()
        return user


def get_auth_provider(s: Settings | None = None) -> AuthProvider:
    s = s or get_settings()
    return LdapAuthProvider(s) if s.auth_provider == "ldap" else LocalAuthProvider(s)


@dataclass(frozen=True)
class SessionData:
    user_id: int
    organization_id: int


class SessionStore:
    """Сессия — случайный токен в Redis с TTL; отзывается удалением ключа."""

    PREFIX = "session:"

    def __init__(self, redis: Redis, ttl_minutes: int) -> None:
        self.redis = redis
        self.ttl = ttl_minutes * 60

    def create(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        data = {"user_id": user.id, "organization_id": user.organization_id}
        self.redis.set(self.PREFIX + token, json.dumps(data), ex=self.ttl)
        return token

    def get(self, token: str) -> SessionData | None:
        raw = self.redis.get(self.PREFIX + token)
        if raw is None:
            return None
        self.redis.expire(self.PREFIX + token, self.ttl)  # скользящий срок
        return SessionData(**json.loads(raw))

    def delete(self, token: str) -> None:
        self.redis.delete(self.PREFIX + token)


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def get_session_store() -> SessionStore:
    return SessionStore(get_redis(), get_settings().session_ttl_minutes)
