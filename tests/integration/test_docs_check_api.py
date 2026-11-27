"""Модуль 1: нормативная база (две редакции 59-ФЗ, 152-ФЗ, отменённый 24-ФЗ) и проверка документа."""

import io
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from gosdoc import cli
from gosdoc.core.config import get_settings
from gosdoc.core.queue import get_enqueue
from gosdoc.core.storage import get_storage
from gosdoc.docs_check.models import NpaAct
from gosdoc.docs_check.npa import PgTextIndex, SearchBackends, find_act
from gosdoc.docs_check.npa_refs import canonical_key
from gosdoc.docs_check.service import run_check_job, run_index_job
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import MakeUser, login
from tests.npa_fakes import backends, checking_model

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


def test_check_without_elasticsearch_and_vector_store(
    client: TestClient,
    sessions: sessionmaker[Session],
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без внешних индексов нормы ищутся в PostgreSQL, эмбеддинги не запрашиваются."""
    prompts: list[str] = []
    llm = checking_model(prompts, embeddings=False)
    search = SearchBackends(PgTextIndex(sessions), None, llm)
    storage = client.app.dependency_overrides[get_storage]()  # type: ignore[attr-defined]

    def run_now(queue: str, actor: str, item_id: int) -> None:
        run = run_index_job if actor == "index_npa_edition" else run_check_job
        run(sessions, storage, search, get_settings(), item_id)

    client.app.dependency_overrides[get_enqueue] = lambda: run_now  # type: ignore[attr-defined]
    org = 2000
    login(client, make_user(roles=["npa_admin", "user"], org_id=org).login)
    assert all(x["ok"] for x in upload_npa(client, ["59-fz-2006.docx", "59-fz-2024.docx"]))
    r = client.post(
        "/api/files", files={"file": ("порядок.docx", (NPA / "checked-document.docx").read_bytes())}
    )
    job = client.post("/api/docs-check/jobs", json={"file_ids": [r.json()["id"]]}).json()[0]
    report = client.get(f"/api/docs-check/jobs/{job['id']}/report")
    assert report.status_code == 200, client.get("/api/docs-check/jobs").json()
    content = [f for f in report.json()["findings"] if f["kind"] == "content"]
    assert [f["quote"] for f in content] == [
        "рассматривается в течение 30 дней со дня его регистрации"
    ]
    # Команда переиндексации (переход на Elasticsearch/Milvus и обратно): всё в очередь, ждёт итог.
    monkeypatch.setattr("gosdoc.cli.enqueue", run_now)
    cli.main(["reindex-npa", "--wait"])  # код возврата зависит от редакций других тестов
    acts = [a for a in client.get("/api/npa/acts").json() if a["organization_id"] == org]
    assert acts and all(e["status"] == "indexed" for a in acts for e in a["editions"])
    # Морфология: слова запроса в других формах, чем в тексте нормы.
    assert PgTextIndex(sessions).search("рассмотрение обращения", org, 5)
    # Чужой организации ведомственные акты не находятся.
    assert PgTextIndex(sessions).search("рассмотрение обращения в течение 30 дней", org, 5)
    assert not PgTextIndex(sessions).search("рассмотрение обращения в течение 30 дней", org + 1, 5)


def test_same_law_number_in_different_years(sessions: sessionmaker[Session]) -> None:
    """273-ФЗ 2008 года и 273-ФЗ 2012 года — разные акты; ссылка без даты неоднозначна."""
    from datetime import date

    with sessions.begin() as db:
        for adopted, number in (
            (date(2008, 12, 25), "273-ФЗ"),
            (date(2012, 12, 29), "273-ФЗ"),
            (date(2006, 5, 2), "59-ФЗ"),
        ):
            db.add(
                NpaAct(
                    organization_id=None,
                    kind="federal_law",
                    number=number,
                    adopted_at=adopted,
                    title="",
                    canonical_key=canonical_key("federal_law", number, adopted),
                )
            )
    with sessions() as db:
        found = find_act(db, 1, canonical_key("federal_law", "273-ФЗ", date(2012, 12, 29)))
        assert found is not None and found.adopted_at == date(2012, 12, 29)
        assert find_act(db, 1, canonical_key("federal_law", "273-ФЗ", None)) is None
        # Единственный закон с таким номером находится и без даты, и с неверным годом
        assert find_act(db, 1, canonical_key("federal_law", "59-ФЗ", None)) is not None
        assert find_act(db, 1, canonical_key("federal_law", "59-ФЗ", date(2007, 5, 2))) is not None
