"""Общие фикстуры. Интеграционные тесты поднимают PostgreSQL в Docker (testcontainers)."""

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-horse-42"

os.environ.setdefault("ENVIRONMENT", "test")


def reset_caches() -> None:
    from gosdoc.core import auth, config, db, storage

    for fn in (
        config.get_settings,
        db.get_engine,
        db.get_sessionmaker,
        storage.get_storage,
        auth.get_redis,
    ):
        fn.cache_clear()


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    from alembic import command
    from alembic.config import Config
    from testcontainers.postgres import PostgresContainer

    image = os.environ.get("TC_POSTGRES_IMAGE", "postgres:16-alpine")
    with PostgresContainer(image, driver="psycopg") as pg:
        url = pg.get_connection_url()
        os.environ["DATABASE_URL"] = url
        reset_caches()
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        yield url


@pytest.fixture
def sessions(pg_url: str):  # type: ignore[no-untyped-def]
    from gosdoc.core.db import get_sessionmaker

    return get_sessionmaker()


@pytest.fixture
def make_user(sessions) -> Callable[..., object]:  # type: ignore[no-untyped-def]
    """Создаёт пользователя с уникальным логином (пользователей не удаляем: на них ссылается аудит)."""
    from gosdoc.core.auth import hash_password
    from gosdoc.core.db import Organization, User

    def make(roles: list[str] | None = None, org_id: int = 1, must_change: bool = False) -> User:
        with sessions.begin() as db:
            if db.get(Organization, org_id) is None:
                db.add(Organization(id=org_id, name=f"Орг {org_id}", short_name=f"О{org_id}"))
                db.flush()
            u = User(
                login=f"u{uuid.uuid4().hex[:10]}",
                full_name="Иванов Иван Иванович",
                organization_id=org_id,
                roles=roles or ["user"],
                password_hash=hash_password(PASSWORD),
                auth_source="local",
                failed_attempts=0,
                is_active=True,
                must_change_password=must_change,
            )
            db.add(u)
        return u

    return make


@pytest.fixture
def client(pg_url: str, tmp_path: Path) -> TestClient:
    from gosdoc.core.auth import SessionStore, get_session_store
    from gosdoc.core.storage import LocalStorage, get_storage
    from gosdoc.main import create_app

    app = create_app()
    store = SessionStore(fakeredis.FakeRedis(), ttl_minutes=60)
    storage = LocalStorage(str(tmp_path / "files"))
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_storage] = lambda: storage
    return TestClient(app)


def login(client: TestClient, user_login: str, password: str = PASSWORD) -> None:
    r = client.post("/api/auth/login", json={"login": user_login, "password": password})
    assert r.status_code == 200, r.text
