"""Нормативная база: ежедневная доиндексация и еженедельная полная переиндексация.

Новые акты и редакции загружаются через раздел «Нормативная база» и индексируются
сразу; DAG повторяет то, что не проиндексировалось (сбой Elasticsearch / модели
эмбеддингов), а по воскресеньям переиндексирует всю базу (смена модели эмбеддингов,
обновление анализатора).
"""

from datetime import datetime

from airflow.decorators import dag, task
from gosdoc_common import DEFAULT_ARGS, call, report


@dag(
    dag_id="gosdoc_npa_update",
    schedule="0 2 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["gosdoc", "npa"],
)
def gosdoc_npa_update() -> None:
    @task
    def reindex(logical_date: datetime | None = None) -> int:
        full = bool(logical_date and logical_date.weekday() == 6)
        queued = int(call("POST", f"npa/reindex?full={'true' if full else 'false'}")["queued"])
        report("npa_reindex", queued=queued, full=full)
        return queued

    reindex()


gosdoc_npa_update()
