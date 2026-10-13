"""Общее для DAG ГосДок: вызов служебного API приложения.

Настройка в Airflow (Admin → Variables):
  gosdoc_api_url        — https://gosdoc.<домен> (адрес фронтенда/ingress)
  gosdoc_service_token  — значение SERVICE_TOKEN приложения (храните в Secrets Backend)
  gosdoc_ca_bundle      — путь к корневому CA на воркерах Airflow (необязательно)
Только стандартная библиотека: в заказанный Airflow ничего ставить не нужно.
"""

import json
import ssl
import urllib.request
from typing import Any

DEFAULT_ARGS = {"owner": "gosdoc", "retries": 2}


def call(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 60) -> Any:
    from airflow.models import Variable

    base = Variable.get("gosdoc_api_url").rstrip("/")
    token = Variable.get("gosdoc_service_token")
    ca = Variable.get("gosdoc_ca_bundle", default_var=None)
    context = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 — адрес из настроек Airflow
        f"{base}/api/internal/{path.lstrip('/')}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
        raw = response.read()
    return json.loads(raw) if raw else None


def report(task: str, **details: Any) -> None:
    """Запуск регламентной задачи — в журнал аудита приложения."""
    call("POST", "audit", {"task": task, **details})
