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
from gosdoc.core.llm import LLMClient
from gosdoc.core.storage import get_storage

log = logging.getLogger(__name__)

# Без них API бесполезен (вход, сессии). Остальные — «деградация»: под остаётся
# в балансировке, а модули, которым зависимость нужна, вернут понятную ошибку.
CRITICAL = ("postgres", "redis")
_VECTOR_HEALTH_PATH = {"chroma": "/api/v2/heartbeat", "milvus": "/healthz"}


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

    def http(url: str) -> Callable[[], None]:
        def check() -> None:
            httpx.get(url, timeout=s.deps_check_timeout_s, verify=verify).raise_for_status()

        return check

    def redis() -> None:
        get_redis().ping()

    return {
        "postgres": postgres,
        "redis": redis,
        "rabbitmq": rabbitmq,
        "storage": lambda: get_storage().ping(),
        "elasticsearch": http(s.elasticsearch_url),
        "vector_store": http(s.vector_store_url.rstrip("/") + _VECTOR_HEALTH_PATH[s.vector_store]),
        "llm": lambda: LLMClient(s).ping(),
    }


def check_dependencies(s: Settings) -> dict[str, str]:
    """{имя: "ok" | текст ошибки}; проверки идут параллельно."""

    def run(fn: Callable[[], None]) -> str:
        try:
            fn()
        except Exception as e:  # любой сбой зависимости = «недоступна»
            return f"{type(e).__name__}: {e}"[:300]
        return "ok"

    checks = _checks(s)
    with ThreadPoolExecutor(len(checks)) as pool:
        results = dict(zip(checks, pool.map(run, checks.values()), strict=True))
    return results


def wait_for_dependencies(s: Settings, timeout_s: float = 300, interval_s: float = 5) -> bool:
    """Ждёт, пока все зависимости станут доступны. False — не дождались."""
    deadline = time.monotonic() + timeout_s
    while True:
        results = check_dependencies(s)
        failed = {k: v for k, v in results.items() if v != "ok"}
        if not failed:
            log.info("Все зависимости доступны")
            return True
        if time.monotonic() > deadline:
            log.error("Зависимости недоступны: %s", failed)
            return False
        log.warning("Ожидание зависимостей: %s", ", ".join(failed))
        time.sleep(interval_s)
