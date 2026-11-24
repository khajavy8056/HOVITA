from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.agents.identity_studio import IdentityStudio
from hovita.agents.producer_agent import ProducerPipeline, SimulatedStageCrashError
from hovita.config.secrets import SecretStore
from hovita.contracts.base import CostMetrics
from hovita.contracts.modules import LLMInput, LLMMessage
from hovita.db.models import CharacterProfile, JobRun, StageCheckpoint
from hovita.economy.key_rotator import KeyRotator
from hovita.economy.ledger import get_daily_spend_usd, record_job_cost
from hovita.modules.bootstrap import build_default_registry
from hovita.modules.llm.router import run_llm_with_failover
from hovita.resources.gpu_manager import GPUManager

CID = "persona-test-001"


@pytest.mark.asyncio
async def test_mid_pipeline_crash_resumes_without_rerunning_prior_stages(
    db_session: Session,
) -> None:
    reg = build_default_registry()
    studio = IdentityStudio(reg)
    await studio.generate_multi_angle_identity(db_session, character_id=CID, approve=True)
    await studio.audition_and_lock_voice(db_session, character_id=CID, approve=True)

    profile = db_session.get(CharacterProfile, CID)
    assert profile is not None
    profile.autonomy_level = "L2"
    profile.publish_mode = "autonomous"
    db_session.commit()

    pipeline = ProducerPipeline(reg)
    first_run_stages: list[str] = []

    with pytest.raises(SimulatedStageCrashError):
        await pipeline.run_pipeline(
            db_session,
            character_id=CID,
            topic="تست ریکاوری میانه پایپلاین",
            platform="sandbox",
            thread_id="crash-resume-thread-1",
            inject_crash_at_stage="04_video_motion",
            executed_stages_log=first_run_stages,
        )

    assert first_run_stages == ["01_idea_research", "02_script_variants", "03_image_gen"]
    cps = list(
        db_session.execute(
            select(StageCheckpoint).where(StageCheckpoint.thread_id == "crash-resume-thread-1")
        ).scalars()
    )
    assert any(c.stage_name == "04_video_motion" and c.status == "failed" for c in cps)

    existing_content_id = cps[0].content_id
    resumed_stages: list[str] = []
    res = await pipeline.run_pipeline(
        db_session,
        character_id=CID,
        topic="تست ریکاوری میانه پایپلاین",
        platform="sandbox",
        thread_id="crash-resume-thread-1",
        content_id=existing_content_id,
        executed_stages_log=resumed_stages,
    )
    assert res["status"] == "published"
    assert "01_idea_research" not in resumed_stages
    assert "02_script_variants" not in resumed_stages
    assert "03_image_gen" not in resumed_stages
    assert resumed_stages[0] == "04_video_motion"
    assert "11_analytics" in resumed_stages


@pytest.mark.asyncio
async def test_rate_limit_429_key_rotation_and_s0_to_s4_failover(
    db_session: Session,
) -> None:
    store = SecretStore()
    keys = ["k1-rate-limited", "k2-healthy"]
    store.set_provider_keys("groq", keys)
    rotator = KeyRotator()
    rotator.register_keys("groq", keys)

    # Cooldown key 0 after 429
    rotator.record_error("groq", 0, status_code=429)
    picked = rotator.next_available_key("groq", keys)
    assert picked is not None
    assert picked[0] == 1 and picked[1] == "k2-healthy"

    # Full S0->S4 failover returns valid structured output even with no live API keys
    reg = build_default_registry(rotator=rotator, secret_store=store)
    res = await run_llm_with_failover(
        reg,
        LLMInput(messages=[LLMMessage(role="user", content="سلام هویتا")], task_complexity="light"),
        session=db_session,
        character_id=CID,
        pipeline="resilience_test",
    )
    assert len(res.data.text) > 0


def test_gpu_on_demand_10min_idle_sleep_and_billing_variance_lt_5pct(
    db_session: Session,
) -> None:
    gpu = GPUManager()
    gpu.gpu_available = True
    gpu.mode = "on_demand"

    res = gpu.dispatch_or_fallback(
        db_session, character_id=CID, task_type="flux_img", priority=1, gpu_minutes_est=2.0
    )
    assert res["execution_target"] == "local_gpu"
    assert gpu.state == "awake"
    assert float(res["energy_kwh"]) > 0.0

    assert (
        gpu.check_idle_sleep(
            db_session,
            character_id=CID,
            now=datetime.now(UTC) + timedelta(minutes=11),
        )
        is True
    )
    assert gpu.state == "sleeping"

    gpu.gpu_available = False
    r_urgent = gpu.dispatch_or_fallback(
        db_session, character_id=CID, task_type="urgent_reel", priority=1
    )
    assert r_urgent["execution_target"] == "eco_api_fallback"

    c1 = CostMetrics(
        tokens_in=2000,
        rate_in_per_1k=0.001,
        tokens_out=500,
        rate_out_per_1k=0.002,
        sec_video=10.0,
        rate_video_per_sec=0.01,
        images=2,
        rate_image=0.02,
        gpu_min=1.0,
        rate_gpu_per_min=0.005,
    )
    expected = round(c1.total_usd, 6)
    record_job_cost(
        db_session,
        character_id=CID,
        pipeline="billing_audit",
        status="ok",
        provider_id="test",
        model_name="test",
        cost=c1,
    )
    recorded = get_daily_spend_usd(db_session, CID)
    variance_pct = abs(recorded - expected) / expected * 100.0
    assert variance_pct < 5.0
    rows = list(db_session.execute(select(JobRun)).scalars())
    assert len(rows) >= 1
