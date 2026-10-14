"""Фоновые задачи dramatiq. Запуск: dramatiq workers.actors"""

import dramatiq
from gosdoc.core.config import get_settings
from gosdoc.core.db import get_sessionmaker
from gosdoc.core.files import cleanup_expired_files
from gosdoc.core.logging import setup_logging
from gosdoc.core.queue import get_broker
from gosdoc.core.storage import get_storage
from gosdoc.docs_check.npa import make_backends
from gosdoc.docs_check.service import run_check_job, run_index_job
from gosdoc.letters.service import run_pdf_job
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


@dramatiq.actor(queue_name="letters", max_retries=0, time_limit=5 * 60 * 1000)
def process_letter_pdf(job_id: int) -> None:
    run_pdf_job(get_sessionmaker(), get_storage(), get_settings(), job_id)


@dramatiq.actor(queue_name="npa", max_retries=0, time_limit=60 * 60 * 1000)
def index_npa_edition(edition_id: int) -> None:
    run_index_job(
        get_sessionmaker(), get_storage(), make_backends(get_settings()), get_settings(), edition_id
    )


@dramatiq.actor(queue_name="docs_check", max_retries=0, time_limit=60 * 60 * 1000)
def process_docs_check(job_id: int) -> None:
    run_check_job(
        get_sessionmaker(), get_storage(), make_backends(get_settings()), get_settings(), job_id
    )
