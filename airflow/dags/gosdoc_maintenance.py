"""Очистка: удаление файлов с истёкшим сроком хранения (ПДн — 30 дней, FILE_RETENTION_DAYS).

Срок хранения реализован кодом приложения, а не правилами бакета (ADR-0004).
"""

from datetime import datetime

from airflow.decorators import dag, task
from gosdoc_common import DEFAULT_ARGS, call, report


@dag(
    dag_id="gosdoc_maintenance",
    schedule="30 3 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["gosdoc", "maintenance"],
)
def gosdoc_maintenance() -> None:
    @task
    def cleanup_files() -> None:
        call("POST", "cleanup-files")
        report("cleanup_files")

    cleanup_files()


gosdoc_maintenance()
