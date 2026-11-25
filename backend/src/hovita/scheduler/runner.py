from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.agents.learning_job import LearningLoopJob
from hovita.agents.planner_agent import PlannerAgent
from hovita.agents.producer_agent import ProducerPipeline
from hovita.config.settings import get_settings
from hovita.contracts.modules import SocialAnalyticsInput
from hovita.contracts.registry import ProviderRegistry
from hovita.db.base import get_session_factory, init_db_schema
from hovita.db.models import (
    ApprovalRecord,
    CharacterProfile,
    ContentItem,
    MetricSnapshot,
    SocialAccount,
)
from hovita.economy.budget import BudgetPolicy
from hovita.logging.logger import get_logger
from hovita.modules.bootstrap import build_default_registry
from hovita.resources.gpu_manager import GPUManager
from hovita.security.audit import append_audit_log
from hovita.security.emergency import is_kill_switch_active

log = get_logger("hovita.scheduler")


class InternalScheduler:
    """Internal Scheduler (Ch. 7): runs periodic jobs cleanly without n8n dependency.
    - Expire stale pending approvals (>24h -> expired)
    - Check OAuth token expiries (<=24h critical warning)
    - Sync daily official metrics (G16) & run weekly learning cycle (G17)
    - Check GPU 10-minute idle timer and sleep GPU when idle
    - Trigger Planner + Producer cycle when budget allows, persona active, and kill switch off
    """

    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self.registry = registry or build_default_registry()
        self.gpu = GPUManager()
        self.planner = PlannerAgent(self.registry)
        self.producer = ProducerPipeline(self.registry)
        self.learning = LearningLoopJob(self.registry)

    def tick_housekeeping(self, session: Session, *, now: datetime | None = None) -> dict[str, Any]:
        ref_now = now or datetime.now(UTC)
        expired_count = 0
        pending = list(
            session.execute(
                select(ApprovalRecord).where(ApprovalRecord.decision == "pending")
            ).scalars()
        )
        char_id = pending[0].character_id if pending else get_settings().character_id
        for app in pending:
            exp = app.expires_at
            if exp is not None:
                exp_aware = exp if exp.tzinfo else exp.replace(tzinfo=UTC)
                if ref_now >= exp_aware:
                    app.decision = "expired"
                    app.decided_at = ref_now
                    app.comment = "Expired after 24h without human action"
                    content = session.execute(
                        select(ContentItem).where(ContentItem.id == app.content_id)
                    ).scalar_one_or_none()
                    if content:
                        content.status = "expired"
                    expired_count += 1
        session.commit()

        # Check expiring social tokens (<= 24 hours left)
        expiring_tokens: list[dict[str, Any]] = []
        accts = list(
            session.execute(
                select(SocialAccount).where(SocialAccount.character_id == char_id)
            ).scalars()
        )
        for acct in accts:
            if acct.token_expires_at is not None:
                exp_aware = (
                    acct.token_expires_at
                    if acct.token_expires_at.tzinfo
                    else acct.token_expires_at.replace(tzinfo=UTC)
                )
                if exp_aware - ref_now <= timedelta(hours=24):
                    expiring_tokens.append(
                        {
                            "platform": acct.platform,
                            "handle": acct.handle,
                            "expires_at": exp_aware.isoformat(),
                        }
                    )
                    append_audit_log(
                        session,
                        character_id=char_id,
                        actor="scheduler",
                        action="social_token_expiring_soon",
                        resource_type="social_account",
                        resource_id=acct.id,
                        details={"platform": acct.platform, "expires_at": exp_aware.isoformat()},
                    )

        slept = self.gpu.check_idle_sleep(session, character_id=char_id, now=ref_now)
        return {
            "expired_approvals": expired_count,
            "expiring_tokens": expiring_tokens,
            "gpu_auto_slept": slept,
            "gpu": self.gpu.status(session).model_dump(mode="json"),
        }

    async def sync_official_metrics(self, session: Session, *, character_id: str) -> dict[str, Any]:
        """G16: Pull official metrics from connected platforms & store daily snapshots."""
        accts = list(
            session.execute(
                select(SocialAccount).where(SocialAccount.character_id == character_id)
            ).scalars()
        )
        analytics = self.registry.ordered_candidates("social_analytics")[0]
        synced = 0
        for acct in accts:
            if acct.platform not in {"instagram", "tiktok", "youtube"}:
                continue
            res = await analytics.fetch(
                SocialAnalyticsInput(
                    character_id=character_id,
                    platform=acct.platform,  # type: ignore[arg-type]
                    account_handle=acct.handle,
                    action="page_insights",
                )
            )
            for m in res.data.metrics:
                session.add(
                    MetricSnapshot(
                        character_id=character_id,
                        account=acct.handle,
                        platform=acct.platform,
                        date=datetime.now(UTC).date().isoformat(),
                        views=m.views,
                        likes=m.likes,
                        comments=m.comments,
                        saves=m.saves,
                        shares=m.shares,
                        follows=m.followers_delta,
                        sales_count=m.sales_count,
                        revenue_usd=m.revenue_usd,
                        confidence=m.confidence,
                    )
                )
                synced += 1
        session.commit()
        return {"accounts_checked": len(accts), "snapshots_recorded": synced}

    async def run_autonomous_cycle(
        self, session: Session, *, character_id: str
    ) -> dict[str, Any]:
        settings = get_settings()
        profile = session.execute(
            select(CharacterProfile).where(CharacterProfile.id == character_id)
        ).scalar_one_or_none()
        if profile is None:
            return {"skipped": "no_persona"}
        if is_kill_switch_active(session) or profile.status == "paused":
            return {"skipped": "emergency_stop_or_paused"}

        budget = BudgetPolicy()
        allowed, b_status = budget.can_run_job(session, character_id, priority="normal")
        if not allowed:
            log.warning("daily_budget_hard_stop_skip_cycle", extra={"character_id": character_id})
            return {"skipped": "daily_budget_exhausted", "spent_usd": b_status.daily_spend_usd}

        plan = await self.planner.plan_next_content(
            session,
            character_id=character_id,
            thread_id=f"sched_{int(now_ts())}",
            preferred_platform="sandbox" if settings.hovita_env == "test" else "instagram",
        )
        task = plan["task_graph"][0]
        prod = await self.producer.run_pipeline(
            session,
            character_id=character_id,
            topic=task["topic"],
            mode=task["production_mode"],
            platform=task["platform"],
            product_sku=task.get("product_sku"),
        )
        return {"plan": plan, "production": prod}


def now_ts() -> float:
    return datetime.now(UTC).timestamp()


async def run_scheduler_loop(session_factory, *, interval_seconds: int = 300) -> None:  # type: ignore[no-untyped-def]
    sched = InternalScheduler()
    while True:
        try:
            with session_factory() as session:
                sched.tick_housekeeping(session)
        except Exception as exc:
            log.error("scheduler_tick_error", extra={"error": str(exc)})
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    settings = get_settings()
    init_db_schema()
    log.info("Starting HOVITA internal scheduler daemon")
    asyncio.run(
        run_scheduler_loop(
            get_session_factory(), interval_seconds=settings.scheduler_interval_sec
        )
    )
