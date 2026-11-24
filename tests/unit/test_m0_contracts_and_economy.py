from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from hovita.contracts.base import (
    CostMetrics,
    DegradationLevel,
    ModuleResult,
    ProviderMetadata,
    ProviderTier,
)
from hovita.contracts.brief import PersonaBrief
from hovita.contracts.modules import MODULE_NAMES, LLMInput, LLMOutput, export_all_module_schemas
from hovita.contracts.registry import ProviderRegistry
from hovita.contracts.workflow import AUTONOMY_TO_PUBLISH_MODE, PUBLISH_MODE_TO_AUTONOMY
from hovita.economy.budget import BudgetPolicy
from hovita.economy.key_rotator import KeyRotator
from hovita.economy.ledger import calculate_operation_cost, record_job_cost
from hovita.resources.gpu_manager import GPUManager


def test_all_12_module_schemas_exported() -> None:
    schemas = export_all_module_schemas()
    assert set(schemas.keys()) == set(MODULE_NAMES)
    assert len(schemas) == 12
    for name, pair in schemas.items():
        assert "properties" in pair["input_schema"], name
        assert "properties" in pair["output_schema"], name


def test_persona_brief_and_autonomy_mapping() -> None:
    brief = PersonaBrief(goal="رشد ۵۰۰ هزار بازدید در ۳۰ روز")
    assert brief.brief_version == "1.0"
    assert PUBLISH_MODE_TO_AUTONOMY["produce_only"] == "L0"
    assert PUBLISH_MODE_TO_AUTONOMY["approval_required"] == "L1"
    assert PUBLISH_MODE_TO_AUTONOMY["autonomous"] == "L2"
    assert AUTONOMY_TO_PUBLISH_MODE["L3"] == "autonomous"


def test_cost_formula_exact_and_budget_80pct_alert(db_session: Session) -> None:
    cost = CostMetrics(
        tokens_in=1000,
        rate_in=0.0005,
        tokens_out=500,
        rate_out=0.001,
        sec_video=10.0,
        rate_video=0.05,
        images=2,
        rate_image=0.04,
        min_call=1.0,
        rate_call=0.10,
        gpu_min=2.0,
        rate_gpu=0.01,
    )
    expected = 1000 * 0.0005 + 500 * 0.001 + 10 * 0.05 + 2 * 0.04 + 1 * 0.10 + 2 * 0.01
    assert abs(calculate_operation_cost(cost) - expected) < 1e-6

    # Record $2.45 out of $3.00 daily budget (>80%)
    record_job_cost(
        db_session,
        character_id="persona-test-001",
        pipeline="production",
        status="completed",
        provider_id="fal",
        model_name="wan-2.2",
        cost=CostMetrics(sec_video=49.0, rate_video=0.05),
    )
    policy = BudgetPolicy()
    can_low, status = policy.can_run_job(db_session, "persona-test-001", priority="low")
    can_crit, _ = policy.can_run_job(db_session, "persona-test-001", priority="critical")
    assert status.warning_80pct is True
    assert status.defer_non_critical is True
    assert can_low is False
    assert can_crit is True
    assert policy.can_revise_content(0) is True
    assert policy.can_revise_content(1) is True
    assert policy.can_revise_content(2) is False  # max 2 revisions


def test_provider_registry_free_first_and_25pct_gate() -> None:
    class DummyProvider:
        def __init__(self, pid: str, tier: ProviderTier, lvl: DegradationLevel, q: float) -> None:
            self.metadata = ProviderMetadata(
                module_name="llm",
                provider_id=pid,
                display_name=pid,
                tier=tier,
                degradation_level=lvl,
                license_name="MIT",
                is_local=(lvl == DegradationLevel.S0_LOCAL),
                quality_score=q,
            )

        async def generate(self, payload: LLMInput) -> ModuleResult[LLMOutput]:
            _ = payload
            return ModuleResult(
                module_name="llm",
                provider_id=self.metadata.provider_id,
                data=LLMOutput(text="ok"),
            )

    reg = ProviderRegistry()
    reg.register("llm", DummyProvider("claude_pro", ProviderTier.PRO, DegradationLevel.S3_PRO, 0.96))
    reg.register("llm", DummyProvider("ollama_local", ProviderTier.FREE, DegradationLevel.S0_LOCAL, 0.72))
    reg.register("llm", DummyProvider("openrouter_eco", ProviderTier.ECO, DegradationLevel.S2_ECO, 0.85))

    ordered = reg.ordered_candidates("llm", policy="free_first")
    assert [p.metadata.provider_id for p in ordered] == [
        "ollama_local",
        "openrouter_eco",
        "claude_pro",
    ]
    # 0.72 -> 0.85 is +18% (<25%), so cannot upgrade without QA failure
    assert reg.can_upgrade_to_paid("llm", "ollama_local", "openrouter_eco") is False
    # 0.72 -> 0.96 is +33.3% (>=25%), allowed
    assert reg.can_upgrade_to_paid("llm", "ollama_local", "claude_pro") is True
    # QA failure overrides and allows upgrade
    assert (
        reg.can_upgrade_to_paid("llm", "ollama_local", "openrouter_eco", qa_failed_on_free=True)
        is True
    )


def test_key_rotator_429_cooldown_and_failover() -> None:
    kr = KeyRotator()
    keys = ["key-alpha-1111", "key-beta-2222"]
    kr.register_keys("openrouter", keys, rpd_limit=10)
    idx, key = kr.next_available_key("openrouter", keys) or (-1, "")
    assert idx == 0 and key == "key-alpha-1111"

    cooldown = kr.record_error("openrouter", 0, status_code=429)
    assert cooldown >= 30

    idx2, key2 = kr.next_available_key("openrouter", keys) or (-1, "")
    assert idx2 == 1 and key2 == "key-beta-2222"


def test_gpu_manager_wake_idle_sleep_and_eco_fallback(db_session: Session) -> None:
    gm = GPUManager()
    gm.gpu_available = True
    res = gm.dispatch_or_fallback(
        db_session,
        character_id="persona-test-001",
        task_type="image_gen",
        priority=1,
        gpu_minutes_est=1.5,
    )
    assert res["execution_target"] == "local_gpu"
    assert float(res["energy_kwh"]) > 0.0  # type: ignore[arg-type]
    assert gm.state == "awake"

    # Simulate 11 minutes idle -> auto-sleep
    future = datetime.now(UTC) + timedelta(minutes=11)
    slept = gm.check_idle_sleep(db_session, character_id="persona-test-001", now=future)
    assert slept is True
    assert gm.state == "sleeping"

    # Without GPU, urgent job falls back to eco API
    gm.gpu_available = False
    res_fb = gm.dispatch_or_fallback(
        db_session,
        character_id="persona-test-001",
        task_type="video_gen",
        priority=1,
    )
    assert res_fb["execution_target"] == "eco_api_fallback"
