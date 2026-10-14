"""Модуль 1: нормативная база (две редакции 59-ФЗ, 152-ФЗ, отменённый 24-ФЗ) и проверка документа."""

import io
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from gosdoc.core.config import get_settings
from gosdoc.core.queue import get_enqueue
from gosdoc.core.storage import get_storage
from gosdoc.docs_check.service import run_check_job, run_index_job
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import MakeUser, login
from tests.npa_fakes import backends

pytestmark = pytest.mark.integration
NPA = Path(__file__).parent.parent / "fixtures" / "npa"


@pytest.fixture
def prompts() -> list[str]:
    return []


@pytest.fixture
def check_client(
    client: TestClient, sessions: sessionmaker[Session], prompts: list[str]
) -> TestClient:
    app = client.app
    storage = app.dependency_overrides[get_storage]()  # type: ignore[attr-defined]
    search = backends(prompts)

    def run_now(queue: str, actor: str, item_id: int) -> None:
        if actor == "index_npa_edition":
            run_index_job(sessions, storage, search, get_settings(), item_id)
        else:
            assert actor == "process_docs_check"
            run_check_job(sessions, storage, search, get_settings(), item_id)

    app.dependency_overrides[get_enqueue] = lambda: run_now  # type: ignore[attr-defined]
    return client


def upload_npa(c: TestClient, names: list[str], **form: str) -> list[dict]:  # type: ignore[type-arg]
    files = [("files", (n, (NPA / n).read_bytes())) for n in names]
    r = c.post("/api/npa/upload", files=files, data=form)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_outdated_reference_found_with_quote(
    check_client: TestClient, make_user: MakeUser, prompts: list[str]
) -> None:
    c = check_client
    org = 1000 + len(
        prompts
    )  # своя организация: ведомственные акты не пересекаются с другими тестами
    admin = make_user(roles=["npa_admin", "user"], org_id=org)
    login(c, admin.login)
    loaded = upload_npa(c, ["59-fz-2006.docx", "59-fz-2024.docx", "152-fz.docx", "24-fz.html"])
    assert all(x["ok"] for x in loaded), loaded
    assert "новая редакция с 04.08.2023" in loaded[1]["message"]

    acts = {a["number"]: a for a in c.get("/api/npa/acts").json() if a["organization_id"] == org}
    fz59 = acts["59-ФЗ"]
    assert [e["effective_from"] for e in fz59["editions"]] == ["2006-05-02", "2023-08-04"]
    assert fz59["editions"][0]["effective_to"] == "2023-08-03"
    assert all(e["status"] == "indexed" for a in acts.values() for e in a["editions"])
    r = c.patch(
        f"/api/npa/acts/{acts['24-ФЗ']['id']}",
        json={
            "repealed_at": "2006-08-08",
            "repealed_note": "Утратил силу в связи с принятием Федерального закона от 27.07.2006 № 149-ФЗ",
        },
    )
    assert r.status_code == 200, r.text
    changes = [
        x["change"]
        for x in c.get("/api/npa/changes").json()
        if x["act_id"] in {a["id"] for a in acts.values()}
    ]
    assert {"added", "new_edition", "repealed"} <= set(changes)

    login(c, (user := make_user(org_id=org)).login)
    assert c.post("/api/npa/upload", files=[("files", ("x.docx", b"x"))]).status_code == 403
    r = c.post(
        "/api/files", files={"file": ("порядок.docx", (NPA / "checked-document.docx").read_bytes())}
    )
    job = c.post("/api/docs-check/jobs", json={"file_ids": [r.json()["id"]]}).json()[0]
    report = c.get(f"/api/docs-check/jobs/{job['id']}/report")
    assert report.status_code == 200, c.get("/api/docs-check/jobs").json()
    data = report.json()

    repealed = next(
        f
        for f in data["findings"]
        if "утратившего силу" in f["message"] or "утративший силу" in f["message"]
    )
    assert repealed["severity"] == "critical" and "149-ФЗ" in repealed["quote"]
    outdated = [f for f in data["findings"] if "устаревшую редакцию" in f["message"]]
    assert outdated, data["findings"]
    art12 = next(f for f in outdated if "ст. 12 ч. 1" in f["norm"])
    assert "Текст ст. 12 ч. 1 изменился" in art12["message"]
    assert "в соответствии с их компетенцией" in art12["quote"]  # цитата действующей нормы

    content = [f for f in data["findings"] if f["kind"] == "content"]
    assert (
        len(content) == 1
        and content[0]["quote"] == "рассматривается в течение 30 дней со дня его регистрации"
    )
    assert "ст. 12 ч. 1" in content[0]["norm"] and "60 дней" in content[0]["doc_fragment"]
    assert data["summary"]["unverified_dropped"] == 1  # выдуманная цитата отброшена
    assert data["is_draft"] and data["summary"]["prompt_version"] == "docs-check-content-v1"
    assert not any(
        "152-ФЗ" in f["npa_ref"] for f in data["findings"]
    )  # актуальная ссылка без замечаний
    # В модель уходят только абзацы документа и тексты норм, а не весь документ разом.
    assert prompts and all("АДМИНИСТРАЦИЯ ГОРОДА ЭНСКА" not in p for p in prompts)

    doc = Document(io.BytesIO(c.get(f"/api/files/{data['result_docx_file_id']}/content").content))
    comments = list(doc.comments)
    assert len(comments) == len(data["findings"])
    assert any("Предлагаемая правка: Замените «60 дней» на «30 дней»" in cm.text for cm in comments)
    assert c.get(f"/api/files/{data['report_docx_file_id']}/content").status_code == 200

    finding = content[0]["id"]
    r = c.post(
        f"/api/docs-check/jobs/{job['id']}/findings/{finding}/verdict", json={"verdict": "accepted"}
    )
    assert r.status_code == 200 and r.json()["user_verdict"] == "accepted"

    login(c, make_user(org_id=org).login)  # чужой отчёт не виден
    assert c.get(f"/api/docs-check/jobs/{job['id']}/report").status_code == 404
    assert user.id != admin.id
