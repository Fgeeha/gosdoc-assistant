"""Фоновые задачи dramatiq. Запуск: dramatiq workers.actors"""

import dramatiq
from gosdoc.core.config import get_settings
from gosdoc.core.db import get_sessionmaker
from gosdoc.core.files import cleanup_expired_files
from gosdoc.core.logging import setup_logging
from gosdoc.core.queue import get_broker
from gosdoc.core.storage import get_storage
from gosdoc.tables.service import run_table_job

_settings = get_settings()
setup_logging(_settings.log_level)
dramatiq.set_broker(get_broker())


@dramatiq.actor(queue_name="maintenance", max_retries=3)
def cleanup_files() -> None:
    cleanup_expired_files(get_sessionmaker(), get_storage())


# Без повторов: любой исход (в т. ч. сбой) задача записывает в jobs сама.
@dramatiq.actor(queue_name="tables", max_retries=0, time_limit=30 * 60 * 1000)
def process_table_job(job_id: int) -> None:
    run_table_job(get_sessionmaker(), get_storage(), get_settings(), job_id)
