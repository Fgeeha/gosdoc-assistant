"""Проверка доступности зависимостей: readiness и ожидание на старте (initContainer)."""

import logging
import ssl
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx
import pika
from sqlalchemy import text

from gosdoc.core.auth import get_redis
from gosdoc.core.config import Settings
from gosdoc.core.db import get_engine
from gosdoc.core.storage import get_storage

log = logging.getLogger(__name__)

# Без них API бесполезен (вход, сессии). Остальные — «деградация»: под остаётся
# в балансировке, а модули, которым зависимость нужна, вернут понятную ошибку.
CRITICAL = ("postgres", "redis")
# Признаки ошибок, которые не исчезнут от повторных попыток.
_PERMANENT = ("password authentication failed", "WRONGPASS", "ACCESS_REFUSED")


def _checks(s: Settings) -> dict[str, Callable[[], None]]:
    verify = s.ca_bundle or True

    def postgres() -> None:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))

    def rabbitmq() -> None:
        params = pika.URLParameters(s.rabbitmq_url)
        params.socket_timeout = params.blocked_connection_timeout = s.deps_check_timeout_s
        if s.rabbitmq_url.startswith("amqps") and s.ca_bundle:
            params.ssl_options = pika.SSLOptions(ssl.create_default_context(cafile=s.ca_bundle))
        pika.BlockingConnection(params).close()

    def http(url: str, token: str | None = None) -> Callable[[], None]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        def check() -> None:
            httpx.get(
                url, headers=headers, timeout=s.deps_check_timeout_s, verify=verify
            ).raise_for_status()

        return check

    def vector_store() -> None:
        base = s.vector_store_url.rstrip("/")
        if s.vector_store == "chroma":
            httpx.get(
                f"{base}/api/v2/heartbeat", timeout=s.deps_check_timeout_s, verify=verify
            ).raise_for_status()
            return
        # Milvus: /healthz слушает порт метрик (9091), поэтому проверяем REST API (19530).
        r = httpx.post(
            f"{base}/v2/vectordb/collections/list",
            json={},
            timeout=s.deps_check_timeout_s,
            verify=verify,
        )
        r.raise_for_status()
        if r.json().get("code", 0) != 0:
            raise RuntimeError(f"Milvus: {r.json().get('message')}")

    def redis() -> None:
        get_redis().ping()

    return {
        "postgres": postgres,
        "redis": redis,
        "rabbitmq": rabbitmq,
        "storage": lambda: get_storage().ping(),
        "elasticsearch": http(s.elasticsearch_url),
        "vector_store": vector_store,
        "llm": http(s.llm_base_url.rstrip("/") + "/models", s.llm_api_key),
        **(
            {
                "embeddings": http(
                    s.embeddings_base_url.rstrip("/") + "/models",
                    s.embeddings_api_key or s.llm_api_key,
                )
            }
            if s.embeddings_base_url
            else {}
        ),
    }


def check_dependencies(s: Settings, only: set[str] | None = None) -> dict[str, str]:
    """{имя: "ok" | текст ошибки}; проверки идут параллельно. only — подмножество проверок."""

    def run(fn: Callable[[], None]) -> str:
        try:
            fn()
        except Exception as e:  # любой сбой зависимости = «недоступна»
            return f"{type(e).__name__}: {e}"[:300]
        return "ok"

    checks = {k: v for k, v in _checks(s).items() if only is None or k in only}
    with ThreadPoolExecutor(len(checks)) as pool:
        results = dict(zip(checks, pool.map(run, checks.values()), strict=True))
    return results


def wait_for_dependencies(
    s: Settings, timeout_s: float = 300, interval_s: float = 5, only: set[str] | None = None
) -> bool:
    """Ждёт, пока зависимости станут доступны. False — не дождались."""
    deadline = time.monotonic() + timeout_s
    while True:
        results = check_dependencies(s, only)
        failed = {k: v for k, v in results.items() if v != "ok"}
        if not failed:
            log.info("Все зависимости доступны")
            return True
        if any(m in v for v in failed.values() for m in _PERMANENT):
            # Ожиданием не лечится. Частый случай: POSTGRES_PASSWORD в .env сменили после
            # первого запуска, а Postgres берёт пароль из переменной только при создании тома.
            log.error("Ошибка аутентификации, ожидание прервано: %s", failed)
            return False
        if time.monotonic() > deadline:
            log.error("Зависимости недоступны: %s", failed)
            return False
        log.warning("Ожидание зависимостей: %s", failed)
        time.sleep(interval_s)


def migrations_applied(s: Settings) -> bool:
    """Версия схемы в БД совпадает с последней миграцией в образе."""
    from pathlib import Path

    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
    with get_engine().connect() as c:
        return set(MigrationContext.configure(c).get_current_heads()) == set(scripts.get_heads())
