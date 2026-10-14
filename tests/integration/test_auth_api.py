from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gosdoc.core.config import Settings
from gosdoc.core.db import AuditLog, StoredFile, User
from gosdoc.core.files import cleanup_expired_files, save_file
from gosdoc.core.storage import LocalStorage
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import PASSWORD, MakeUser, login

pytestmark = pytest.mark.integration


def test_login_me_logout(client: TestClient, make_user: MakeUser) -> None:
    u = make_user()
    assert client.get("/api/auth/me").status_code == 401
    login(client, u.login)
    assert client.get("/api/auth/me").json()["login"] == u.login
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_lockout_after_failed_attempts(
    client: TestClient, make_user: MakeUser, sessions: sessionmaker[Session]
) -> None:
    u = make_user()
    for _ in range(5):
        r = client.post("/api/auth/login", json={"login": u.login, "password": "wrong-password-1"})
        assert r.status_code == 401
    assert "заблокирована" in r.json()["detail"]
    # Даже верный пароль не пускает, пока блокировка действует.
    r = client.post("/api/auth/login", json={"login": u.login, "password": PASSWORD})
    assert r.status_code == 401
    with sessions() as db:
        locked = db.get(User, u.id)
        assert locked is not None and locked.locked_until is not None
        failed = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "auth.login_failed", AuditLog.details["login"].astext == u.login
            )
        )
        assert failed == 6


def test_first_login_requires_password_change(client: TestClient, make_user: MakeUser) -> None:
    u = make_user(must_change=True)
    login(client, u.login)
    assert client.get("/api/files/1").status_code == 403
    r = client.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 400
    r = client.post(
        "/api/auth/password", json={"old_password": PASSWORD, "new_password": "New-password-2026"}
    )
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 401  # сессия сброшена
    login(client, u.login, "New-password-2026")
    assert client.get("/api/auth/me").json()["must_change_password"] is False


def test_admin_endpoints_require_role(client: TestClient, make_user: MakeUser) -> None:
    login(client, make_user().login)
    assert client.get("/api/admin/users").status_code == 403

    admin = make_user(roles=["sys_admin"])
    login(client, admin.login)
    r = client.post(
        "/api/admin/users",
        json={
            "login": "petrov.p",
            "full_name": "Петров П.П.",
            "roles": ["user", "manager"],
            "password": "Temp-password-01",
        },
    )
    assert r.status_code in (201, 409), r.text
    bad = client.post(
        "/api/admin/users",
        json={"login": "x1", "full_name": "X", "roles": ["root"], "password": "Temp-password-01"},
    )
    assert bad.status_code == 422


def test_files_are_isolated_by_organization(client: TestClient, make_user: MakeUser) -> None:
    owner = make_user(roles=["user", "manager"], org_id=1)
    login(client, owner.login)
    r = client.post(
        "/api/files", files={"file": ("письмо.txt", "СНИЛС 112-233-445 95".encode(), "text/plain")}
    )
    assert r.status_code == 201, r.text
    file_id = r.json()["id"]
    assert r.json()["expires_at"] is not None
    assert client.get(f"/api/files/{file_id}/content").content.startswith("СНИЛС".encode())

    stranger = make_user(roles=["user", "manager"], org_id=2)
    login(client, stranger.login)
    assert client.get(f"/api/files/{file_id}").status_code == 404


def test_audit_log_is_append_only(sessions: sessionmaker[Session], make_user: MakeUser) -> None:
    make_user()
    with pytest.raises(DBAPIError, match="запрещено"), sessions.begin() as db:
        db.execute(text("UPDATE audit_log SET action = 'x'"))
    with pytest.raises(DBAPIError, match="запрещено"), sessions.begin() as db:
        db.execute(text("DELETE FROM audit_log"))


def test_cleanup_removes_only_expired_files(
    sessions: sessionmaker[Session], make_user: MakeUser, tmp_path: Path
) -> None:
    u = make_user()
    storage = LocalStorage(str(tmp_path))
    now = datetime.now(UTC)
    with sessions.begin() as db:
        for key, expires in (
            ("1/old", now - timedelta(days=1)),
            ("1/new", now + timedelta(days=1)),
            ("1/npa", None),
        ):
            storage.put(key, b"x")
            db.add(
                StoredFile(
                    organization_id=1,
                    owner_id=u.id,
                    storage_key=key,
                    original_name=key,
                    mime="text/plain",
                    size=1,
                    sha256="0" * 64,
                    expires_at=expires,
                )
            )

    assert cleanup_expired_files(sessions, storage, now) >= 1
    assert storage.list("1/") == ["1/new", "1/npa"]
    with sessions() as db:
        keys = set(db.scalars(select(StoredFile.storage_key)))
        assert "1/old" not in keys and {"1/new", "1/npa"} <= keys


def _upload(client: TestClient, name: str = "f.txt") -> int:
    r = client.post("/api/files", files={"file": (name, b"data", "text/plain")})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def test_password_change_revokes_all_sessions(client: TestClient, make_user: MakeUser) -> None:
    u = make_user()
    other = TestClient(client.app)  # второй браузер того же пользователя
    login(client, u.login)
    login(other, u.login)
    r = client.post(
        "/api/auth/password", json={"old_password": PASSWORD, "new_password": "Another-pass-2026"}
    )
    assert r.status_code == 204
    assert other.get("/api/auth/me").status_code == 401


def test_admin_patch_validates_and_disables(client: TestClient, make_user: MakeUser) -> None:
    target = make_user()
    target_client = TestClient(client.app)
    login(target_client, target.login)
    login(client, make_user(roles=["sys_admin"]).login)

    url = f"/api/admin/users/{target.id}"
    assert client.patch(url, json={"full_name": None}).status_code == 422
    assert client.patch(url, json={"department": "x" * 301}).status_code == 422
    assert client.patch(url, json={"roles": ["root"]}).status_code == 422
    r = client.patch(url, json={"department": "Юридический отдел", "is_active": False})
    assert r.status_code == 200, r.text
    assert r.json()["department"] == "Юридический отдел" and r.json()["is_active"] is False
    assert target_client.get("/api/auth/me").status_code == 401  # сессии отозваны


def test_manager_sees_only_own_department(client: TestClient, make_user: MakeUser) -> None:
    owner = make_user(department="Отдел А")
    login(client, owner.login)
    file_id = _upload(client)

    login(client, make_user(roles=["user", "manager"], department="Отдел А").login)
    assert client.get(f"/api/files/{file_id}").status_code == 200
    login(client, make_user(roles=["user", "manager"], department="Отдел Б").login)
    assert client.get(f"/api/files/{file_id}").status_code == 404
    login(client, make_user(department="Отдел А").login)  # не руководитель
    assert client.get(f"/api/files/{file_id}").status_code == 404


def test_rollback_after_upload_removes_content(
    sessions: sessionmaker[Session], make_user: MakeUser, tmp_path: Path
) -> None:
    u = make_user()
    storage = LocalStorage(str(tmp_path))
    with pytest.raises(RuntimeError), sessions.begin() as db:
        save_file(
            db, storage, Settings(), organization_id=1, owner_id=u.id, name="a", mime="", data=b"x"
        )
        assert storage.list("1/")
        raise RuntimeError("сбой после сохранения")
    assert storage.list("1/") == []
