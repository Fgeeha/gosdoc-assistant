"""Служебные эндпоинты для регламентных задач Airflow (не для пользователей).

Доступ — по заголовку Authorization: Bearer <SERVICE_TOKEN>. Ответы не содержат
персональных данных: только идентификаторы, счётчики и метки.
"""

import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from gosdoc.core.audit import audit
from gosdoc.core.config import get_settings
from gosdoc.core.db import Job, get_db
from gosdoc.core.mlflow import MlflowClient, collect_prompts
from gosdoc.core.queue import Enqueue, enqueue_after_commit, get_enqueue
from gosdoc.docs_check.models import CheckReport, Finding, NpaEdition

router = APIRouter(prefix="/api/internal", tags=["internal"], include_in_schema=False)


def service_auth(authorization: str = Header("")) -> None:
    token = get_settings().service_token
    given = authorization.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "Нужен служебный токен")


@router.post("/cleanup-files", dependencies=[Depends(service_auth)], status_code=202)
def cleanup_files(enqueue: Enqueue = Depends(get_enqueue)) -> dict[str, str]:
    enqueue("maintenance", "cleanup_files", 0)
    return {"status": "queued"}


@router.post("/npa/reindex", dependencies=[Depends(service_auth)], status_code=202)
def reindex_npa(
    full: bool = False,
    db: Session = Depends(get_db, scope="function"),
    enqueue: Enqueue = Depends(get_enqueue),
) -> dict[str, int]:
    """Повтор не завершённых индексаций; full=true — переиндексация всей базы."""
    stmt = select(NpaEdition) if full else select(NpaEdition).where(NpaEdition.status != "indexed")
    editions = list(db.scalars(stmt))
    for e in editions:
        e.status = "queued"
    enqueue_after_commit(db, enqueue, "npa", "index_npa_edition", [e.id for e in editions])
    return {"queued": len(editions)}


@router.post("/mlflow/sync-prompts", dependencies=[Depends(service_auth)])
def mlflow_sync() -> dict[str, int]:
    s = get_settings()
    if not s.mlflow_url:
        raise HTTPException(409, "MLFLOW_URL не задан")
    return {"logged": MlflowClient(s).sync(collect_prompts())}


@router.get("/quality/findings", dependencies=[Depends(service_auth)])
def quality_findings(
    days: int = 1, db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    """Обезличенные строки для мониторинга качества (Evidently): без текстов и цитат."""
    since = datetime.now(UTC) - timedelta(days=min(days, 90))
    stmt = (
        select(Finding, CheckReport, Job)
        .join(CheckReport, CheckReport.id == Finding.report_id)
        .join(Job, Job.id == CheckReport.job_id)
        .where(CheckReport.created_at >= since)
    )
    return [
        {
            "finding_id": f.id,
            "organization_id": r.organization_id,
            "created_at": r.created_at.isoformat(),
            "kind": f.kind,
            "severity": f.severity,
            "has_quote": bool(f.quote),
            "verdict": f.user_verdict,
            "prompt_version": r.summary.get("prompt_version"),
            "model_name": r.summary.get("model_name"),
            "unverified_dropped": r.summary.get("unverified_dropped", 0),
            "paragraphs_checked": r.summary.get("paragraphs_checked", 0),
        }
        for f, r, j in db.execute(stmt)
    ]


@router.post("/audit", dependencies=[Depends(service_auth)], status_code=204)
def audit_task(body: dict[str, Any], db: Session = Depends(get_db, scope="function")) -> None:
    """Регламентная задача фиксирует свой запуск в журнале аудита."""
    audit(
        db,
        organization_id=get_settings().default_organization_id,
        action=f"airflow.{str(body.get('task', ''))[:60]}",
        details={k: v for k, v in body.items() if isinstance(v, int | str | bool)},
    )
