import json

import httpx
from gosdoc.core.config import Settings
from gosdoc.core.mlflow import MlflowClient, collect_prompts


def test_sync_logs_each_prompt_version_once() -> None:
    runs: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/2.0/mlflow/")
        if path == "experiments/get-by-name":
            return httpx.Response(404, json={"error_code": "RESOURCE_DOES_NOT_EXIST"})
        body = json.loads(request.content)
        if path == "experiments/create":
            return httpx.Response(200, json={"experiment_id": "7"})
        if path == "runs/search":
            sha = body["filter"].split("'")[1]
            hit = [r for r in runs if r.get("sha") == sha]
            return httpx.Response(200, json={"runs": hit})
        if path == "runs/create":
            runs.append({"id": str(len(runs))})
            return httpx.Response(200, json={"run": {"info": {"run_id": runs[-1]["id"]}}})
        if path == "runs/log-batch":
            params = {p["key"]: p["value"] for p in body["params"]}
            runs[int(body["run_id"])]["sha"] = params["prompt_sha256"]
            assert params["model_name"] == "m1"
        return httpx.Response(200, json={})

    prompts = collect_prompts()
    assert {p.module for p in prompts} == {"tables", "letters", "docs_check"}
    client = MlflowClient(
        Settings(mlflow_url="http://mlflow", llm_model="m1"), transport=httpx.MockTransport(handler)
    )
    assert client.sync(prompts) == len(prompts)
    assert client.sync(prompts) == 0  # повторный запуск ничего не дублирует
