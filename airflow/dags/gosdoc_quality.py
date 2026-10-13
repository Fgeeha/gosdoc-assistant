"""Мониторинг качества: обезличенная статистика замечаний → Evidently, версии промптов → MLFlow.

Метрики: доля замечаний, подтверждённых сотрудниками, по виду и версии промпта;
доля ответов модели без подтверждённой цитаты (unverified_dropped). Тексты
документов и цитаты из приложения не выгружаются.

Variables: gosdoc_evidently_url, gosdoc_evidently_project (для Evidently UI).
Пакет evidently должен быть установлен в окружении Airflow; без него шаг
пропускается с предупреждением.
"""

import logging
from datetime import datetime

from airflow.decorators import dag, task
from gosdoc_common import DEFAULT_ARGS, call, report

log = logging.getLogger(__name__)


@dag(
    dag_id="gosdoc_quality",
    schedule="0 5 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["gosdoc", "quality"],
)
def gosdoc_quality() -> None:
    @task
    def sync_prompts() -> int:
        synced = int(call("POST", "mlflow/sync-prompts")["logged"])
        report("mlflow_sync", logged=synced)
        return synced

    @task
    def findings_to_evidently() -> int:
        rows = call("GET", "quality/findings?days=1") or []
        if not rows:
            return 0
        try:
            import pandas as pd
            from airflow.models import Variable
            from evidently import Report
            from evidently.presets import DataSummaryPreset
            from evidently.ui.workspace import RemoteWorkspace
        except ImportError:
            log.warning("evidently не установлен в окружении Airflow — отчёт качества пропущен")
            return 0
        df = pd.DataFrame(rows)
        df["accepted"] = (df["verdict"] == "accepted").astype(float)
        df["rated"] = df["verdict"].notna().astype(float)
        ws = RemoteWorkspace(Variable.get("gosdoc_evidently_url"))
        project = ws.get_project(Variable.get("gosdoc_evidently_project"))
        run = Report([DataSummaryPreset()]).run(
            current_data=df[
                [
                    "kind",
                    "severity",
                    "has_quote",
                    "accepted",
                    "rated",
                    "unverified_dropped",
                    "prompt_version",
                    "model_name",
                ]
            ]
        )
        ws.add_run(project.id, run, include_data=False)
        report("evidently_report", rows=len(rows))
        return len(rows)

    sync_prompts()
    findings_to_evidently()


gosdoc_quality()
