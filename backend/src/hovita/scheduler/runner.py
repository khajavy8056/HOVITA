from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.agents.learning_job import LearningLoopJob
from hovita.agents.planner_agent import PlannerAgent
from hovita.agents.producer_agent import ProducerPipeline
from hovita.config.settings import get_settings
from hovita.contracts.registry import ProviderRegistry
from hovita.db.models import ApprovalRecord, CharacterProfile, ContentItem
from hovita.economy.budget import BudgetPolicy
from hovita.logging.logger import get_logger
from hovita.modules.bootstrap import build_default_registry
from hovita.resources.gpu_manager import GPUManager
from hovita.security.emergency import is_kill_switch_active

log = get_logger("hovita.scheduler")


class InternalScheduler:
    """Internal Scheduler (Ch. 7): runs periodic jobs cleanly without n8n dependency.
    - Expire stale pending approvals (>24h -> deferred/expired)
    - Check GPU 10-minute idle timer and sleep GPU when idle
    - Trigger Planner + Producer cycle when budget allows and emergency stop is inactive
    """

    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self.registry = registry or build_default_registry()
        self.gpu = GPUManager()
        self.planner = PlannerAgent(self.registry)
        self.producer = ProducerPipeline(self.registry)
        self.learning = LearningLoopJob(self.registry)

    def tick_housekeeping(self, session: Session) -> dict[str, Any]:
        now = datetime.now(UTC)
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
                if now >= exp_aware:
                    app.decision = "expired"
                    app.decided_at = now
                    app.comment = "Expired after 24h without human action"
                    content = session.execute(
                        select(ContentItem).where(ContentItem.id == app.content_id)
                    ).scalar_one_or_none()
                    if content:
                        content.status = "expired"
                    expired_count += 1
        session.commit()
        slept = self.gpu.check_idle_sleep(session, character_id=char_id, now=now)
        return {
            "expired_approvals": expired_count,
            "gpu_auto_slept": slept,
            "gpu": self.gpu.status(session).model_dump(mode="json"),
        }

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
