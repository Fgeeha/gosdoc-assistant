"""Служебные эндпоинты Airflow: доступ только по токену, без персональных данных."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from gosdoc.core.config import get_settings
from gosdoc.core.queue import get_enqueue

pytestmark = pytest.mark.integration
TOKEN = "t" * 40


@pytest.fixture
def internal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list[tuple[str, str, int]]]]:
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    get_settings.cache_clear()
    sent: list[tuple[str, str, int]] = []
    client.app.dependency_overrides[get_enqueue] = lambda: lambda q, a, i: sent.append((q, a, i))  # type: ignore[attr-defined]
    yield client, sent
    monkeypatch.delenv("SERVICE_TOKEN")
    get_settings.cache_clear()


def test_service_token_required(internal: tuple[TestClient, list[tuple[str, str, int]]]) -> None:
    c, sent = internal
    assert c.post("/api/internal/cleanup-files").status_code == 401
    assert (
        c.post("/api/internal/cleanup-files", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert c.post("/api/internal/cleanup-files", headers=auth).status_code == 202
    assert sent == [("maintenance", "cleanup_files", 0)]
    assert c.post("/api/internal/npa/reindex", headers=auth).status_code == 202
    rows = c.get("/api/internal/quality/findings?days=30", headers=auth).json()
    allowed = {
        "finding_id",
        "organization_id",
        "created_at",
        "kind",
        "severity",
        "has_quote",
        "verdict",
        "prompt_version",
        "model_name",
        "unverified_dropped",
        "paragraphs_checked",
    }
    assert all(set(r) == allowed for r in rows)  # ни фрагментов документа, ни цитат
    assert (
        c.post(
            "/api/internal/audit", headers=auth, json={"task": "npa_update", "queued": 3}
        ).status_code
        == 204
    )


def test_migrations_applied(pg_url: str) -> None:
    from gosdoc.core.health import migrations_applied

    assert migrations_applied(get_settings())
