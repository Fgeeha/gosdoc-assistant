"""DAG загружаются и вызывают нужные служебные эндпоинты (Airflow подменяется заглушкой)."""

import importlib
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

DAGS = Path(__file__).parent.parent.parent / "airflow" / "dags"


@pytest.fixture
def airflow_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    registry: dict[str, Any] = {}

    def dag(**kw: Any) -> Callable[[Callable[[], None]], Callable[[], None]]:
        def wrap(fn: Callable[[], None]) -> Callable[[], None]:
            def build() -> None:
                registry[kw["dag_id"]] = {**kw, "tasks": []}
                registry["_current"] = kw["dag_id"]
                fn()

            return build

        return wrap

    def task(fn: Callable[..., Any]) -> Callable[..., Any]:
        def add(*a: Any, **k: Any) -> None:
            registry[registry["_current"]]["tasks"].append(fn)

        return add

    decorators = types.ModuleType("airflow.decorators")
    decorators.dag, decorators.task = dag, task  # type: ignore[attr-defined]
    models = types.ModuleType("airflow.models")
    values = {"gosdoc_api_url": "https://gosdoc.test", "gosdoc_service_token": "x"}
    models.Variable = types.SimpleNamespace(
        get=lambda k, default_var=None: values.get(k, default_var)
    )  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "airflow", types.ModuleType("airflow"))
    monkeypatch.setitem(sys.modules, "airflow.decorators", decorators)
    monkeypatch.setitem(sys.modules, "airflow.models", models)
    monkeypatch.syspath_prepend(str(DAGS))
    for name in ("gosdoc_common", "gosdoc_npa_update", "gosdoc_maintenance", "gosdoc_quality"):
        sys.modules.pop(name, None)
    yield registry


def test_dags_call_internal_api(
    airflow_stub: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    common = importlib.import_module("gosdoc_common")
    calls: list[tuple[str, str]] = []

    def fake_call(method: str, path: str, body: Any = None, timeout: int = 60) -> Any:
        calls.append((method, path))
        return {"queued": 2, "logged": 1} if method == "POST" else []

    monkeypatch.setattr(common, "call", fake_call)
    for name in ("gosdoc_npa_update", "gosdoc_maintenance", "gosdoc_quality"):
        module = importlib.import_module(name)
        monkeypatch.setattr(module, "call", fake_call)
        monkeypatch.setattr(module, "report", lambda *a, **k: None)
    assert {k for k in airflow_stub if not k.startswith("_")} == {
        "gosdoc_npa_update",
        "gosdoc_maintenance",
        "gosdoc_quality",
    }
    assert airflow_stub["gosdoc_npa_update"]["schedule"] == "0 2 * * *"

    from datetime import datetime

    reindex = airflow_stub["gosdoc_npa_update"]["tasks"][0]
    assert reindex(logical_date=datetime(2026, 10, 18)) == 2  # воскресенье — полная переиндексация
    assert reindex(logical_date=datetime(2026, 10, 14)) == 2
    for t in airflow_stub["gosdoc_maintenance"]["tasks"] + airflow_stub["gosdoc_quality"]["tasks"]:
        t()
    assert calls == [
        ("POST", "npa/reindex?full=true"),
        ("POST", "npa/reindex?full=false"),
        ("POST", "cleanup-files"),
        ("POST", "mlflow/sync-prompts"),
        ("GET", "quality/findings?days=1"),
    ]
