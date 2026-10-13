"""Учёт версий промптов и моделей в MLFlow (REST API 2.0, без клиентской библиотеки).

Каждой версии промпта соответствует один run в эксперименте «gosdoc-prompts»:
параметры — версия, модуль, модель, sha256 текста; текст — в теге. Повторный
запуск ничего не дублирует.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from gosdoc.core.config import Settings

EXPERIMENT = "gosdoc-prompts"
MAX_TAG = 5000  # ограничение длины тега в MLFlow


@dataclass(frozen=True)
class PromptVersion:
    module: str
    version: str
    text: str


def collect_prompts() -> list[PromptVersion]:
    from gosdoc.docs_check.checker import _prompt
    from gosdoc.letters.service import letter_types
    from gosdoc.tables.mapping import _PROMPT, PROMPT_VERSION

    out = [PromptVersion("tables", PROMPT_VERSION, _PROMPT)]
    out += [
        PromptVersion("letters", cfg["version"], cfg["system"] + "\n" + cfg["user"])
        for cfg in letter_types().values()
    ]
    p = _prompt()
    out.append(PromptVersion("docs_check", p["version"], p["system"] + "\n" + p["user"]))
    return out


class MlflowClient:
    def __init__(self, s: Settings, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"Authorization": f"Bearer {s.mlflow_token}"} if s.mlflow_token else {}
        self.http = httpx.Client(
            base_url=s.mlflow_url.rstrip("/") + "/api/2.0/mlflow/",
            headers=headers,
            verify=s.ca_bundle or True,
            timeout=15,
            transport=transport,
        )
        self.model = s.llm_model

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        r = self.http.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def experiment_id(self) -> str:
        r = self.http.get("experiments/get-by-name", params={"experiment_name": EXPERIMENT})
        if r.status_code == 404 or (r.status_code == 400 and "RESOURCE_DOES_NOT_EXIST" in r.text):
            return str(self._post("experiments/create", {"name": EXPERIMENT})["experiment_id"])
        r.raise_for_status()
        return str(r.json()["experiment"]["experiment_id"])

    def sync(self, prompts: list[PromptVersion]) -> int:
        exp = self.experiment_id()
        logged = 0
        for p in prompts:
            sha = hashlib.sha256(p.text.encode()).hexdigest()
            found = self._post(
                "runs/search",
                {
                    "experiment_ids": [exp],
                    "filter": f"params.prompt_sha256 = '{sha}' and params.model_name = '{self.model}'",
                    "max_results": 1,
                },
            )
            if found.get("runs"):
                continue
            run = self._post(
                "runs/create",
                {
                    "experiment_id": exp,
                    "run_name": f"{p.version} / {self.model}",
                    "tags": [{"key": "module", "value": p.module}],
                },
            )["run"]["info"]
            self._post(
                "runs/log-batch",
                {
                    "run_id": run["run_id"],
                    "params": [
                        {"key": "prompt_version", "value": p.version},
                        {"key": "module", "value": p.module},
                        {"key": "model_name", "value": self.model},
                        {"key": "prompt_sha256", "value": sha},
                    ],
                    "tags": [{"key": "prompt_text", "value": p.text[:MAX_TAG]}],
                },
            )
            self._post("runs/update", {"run_id": run["run_id"], "status": "FINISHED"})
            logged += 1
        return logged
