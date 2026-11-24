from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hovita.contracts.base import CostMetrics
from hovita.db.models import JobRun
from hovita.logging.logger import get_trace_id


def calculate_operation_cost(metrics: CostMetrics) -> float:
    """Exact economic formula:
    cost = tin*Rin + tout*Rout + sec_video*Rv + img*Ri + min_call*Rc + gpu_min*Rg
    """
    return metrics.total_usd


def record_job_cost(
    session: Session,
    *,
    character_id: str,
    pipeline: str,
    status: str,
    provider_id: str,
    model_name: str,
    cost: CostMetrics,
    duration_ms: int = 0,
    thread_id: str = "",
    content_id: str | None = None,
    current_stage: str = "completed",
    error: str | None = None,
) -> JobRun:
    row = JobRun(
        character_id=character_id,
        pipeline=pipeline,
        thread_id=thread_id,
        content_id=content_id,
        current_stage=current_stage,
        status=status,
        tokens_in=cost.tokens_in,
        tokens_out=cost.tokens_out,
        sec_video=cost.sec_video,
        images=cost.images,
        min_call=cost.min_call,
        gpu_min=cost.gpu_min,
        energy_kwh=cost.energy_kwh,
        cost_usd=cost.total_usd,
        provider_id=provider_id,
        model_name=model_name,
        duration_ms=duration_ms,
        trace_id=get_trace_id(),
        error=error,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_daily_spend_usd(session: Session, character_id: str) -> float:
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = session.execute(
        select(func.coalesce(func.sum(JobRun.cost_usd), 0.0)).where(
            JobRun.character_id == character_id,
            JobRun.created_at >= start_of_day,
        )
    ).scalar_one()
    return round(float(total), 6)


def get_monthly_spend_usd(session: Session, character_id: str) -> float:
    since = datetime.now(UTC) - timedelta(days=30)
    total = session.execute(
        select(func.coalesce(func.sum(JobRun.cost_usd), 0.0)).where(
            JobRun.character_id == character_id,
            JobRun.created_at >= since,
        )
    ).scalar_one()
    return round(float(total), 6)


def get_content_spend_usd(session: Session, content_id: str) -> float:
    total = session.execute(
        select(func.coalesce(func.sum(JobRun.cost_usd), 0.0)).where(
            JobRun.content_id == content_id,
        )
    ).scalar_one()
    return round(float(total), 6)
