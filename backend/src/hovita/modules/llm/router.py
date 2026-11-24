from __future__ import annotations

from sqlalchemy.orm import Session

from hovita.config.settings import get_settings
from hovita.contracts.base import DegradationLevel, ModuleResult, ProviderError, ProviderTier
from hovita.contracts.modules import LLMInput, LLMOutput
from hovita.contracts.registry import ProviderRegistry
from hovita.economy.budget import BudgetPolicy
from hovita.economy.ledger import record_job_cost
from hovita.logging.logger import get_logger

logger = get_logger("hovita.modules.llm.router")


async def run_llm_with_failover(
    registry: ProviderRegistry,
    payload: LLMInput,
    *,
    session: Session | None = None,
    character_id: str | None = None,
    pipeline: str = "llm_call",
    content_id: str | None = None,
    allow_paid: bool = True,
) -> ModuleResult[LLMOutput]:
    """Execute LLM generation across S0 -> S1 -> S2 -> S3 -> S4 degradation ladder."""
    settings = get_settings()
    cid = character_id or settings.character_id

    if session is not None:
        priority = "critical" if payload.task_complexity == "heavy" else "normal"
        can_run, bstat = BudgetPolicy().can_run_job(session, cid, priority=priority)
        if not can_run or bstat.budget_exhausted:
            allow_paid = False

    candidates = registry.ordered_candidates(
        "llm",
        policy=settings.brain_model_gate,
        allow_paid=allow_paid,
    )
    errors: list[str] = []

    for provider in candidates:
        try:
            result: ModuleResult[LLMOutput] = await provider.generate(payload)
            if session is not None:
                record_job_cost(
                    session,
                    character_id=cid,
                    pipeline=pipeline,
                    status="completed",
                    provider_id=result.provider_id,
                    model_name=result.model_name,
                    cost=result.cost,
                    duration_ms=result.latency_ms,
                    content_id=content_id,
                )
            return result
        except ProviderError as exc:
            errors.append(f"{exc.provider_id}:{exc}")
            logger.warning("LLM provider failed, falling back: %s", exc)
            continue

    # S4 Graceful Degradation: guaranteed local deterministic response
    local = registry.get("llm", "ollama_local")
    fallback_res: ModuleResult[LLMOutput] = await local.generate(payload)
    fallback_res.degradation_level = DegradationLevel.S4_DEGRADED
    fallback_res.tier = ProviderTier.FREE
    fallback_res.warnings.extend(["S4_graceful_degradation_engaged", *errors])
    return fallback_res
