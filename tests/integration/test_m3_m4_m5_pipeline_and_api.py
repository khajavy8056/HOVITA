from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.agents.identity_studio import IdentityStudio
from hovita.agents.learning_job import LearningLoopJob
from hovita.agents.live_session import LivePersonaSessionManager
from hovita.agents.planner_agent import PlannerAgent
from hovita.agents.producer_agent import ProducerPipeline
from hovita.agents.quality_gates import QualityGatePipeline
from hovita.agents.social_connect import SocialConnector
from hovita.api.app import app
from hovita.config.secrets import SecretStore
from hovita.contracts.modules import MemoryWriteInput, PublishInput
from hovita.db.models import (
    ApprovalRecord,
    CharacterProfile,
    ContentItem,
    MetricSnapshot,
    SocialAccount,
)
from hovita.modules.bootstrap import build_default_registry
from hovita.modules.publisher.providers import (
    ComplianceViolationError,
    DuplicatePublishError,
    QuotaExceededError,
)
from hovita.security.emergency import clear_emergency_stop, trigger_emergency_stop

CID = "persona-test-001"


@pytest.mark.asyncio
async def test_m3_social_connect_audit_and_8_steps(db_session: Session) -> None:
    reg = build_default_registry()
    conn = SocialConnector(reg)

    status = conn.connect_account(
        db_session,
        character_id=CID,
        platform="instagram",
        handle="@lili_perfume",
        access_token="ig_super_secret_token_999",
        expires_in_days=60,
    )
    assert status["status"] == "connected"
    assert SecretStore().get_primary_key("instagram") == "ig_super_secret_token_999"
    acct = db_session.execute(select(SocialAccount)).scalar_one()
    assert "ig_super_secret_token_999" not in str(acct.meta_json)

    audit = await conn.audit_page_and_run_setup_wizard(
        db_session,
        character_id=CID,
        platform="instagram",
        handle="@lili_perfume",
        page_state="fresh",
    )
    assert audit["detected_state"] == "fresh"
    assert len(audit["findings"]) == 5
    assert len(audit["recommendations"]) == 3
    completed = [s for s in audit["steps"] if s["status"] == "completed"]
    assert len(completed) >= 4


@pytest.mark.asyncio
async def test_m4_11_stage_pipeline_autonomy_l0_l1_l2_and_product_truth(db_session: Session) -> None:
    reg = build_default_registry()
    studio = IdentityStudio(reg)
    await studio.generate_multi_angle_identity(db_session, character_id=CID, approve=True)
    await studio.audition_and_lock_voice(db_session, character_id=CID, approve=True)
    studio.upsert_product(
        db_session,
        character_id=CID,
        sku="PRF-900",
        name="عطر رز",
        price=50.0,
        stock=10,
        buy_link="https://shop.local/prf-900",
    )

    profile = db_session.get(CharacterProfile, CID)
    assert profile is not None
    pipeline = ProducerPipeline(reg)

    # 1. L0 (produce_only) stops in library, never publishes
    profile.autonomy_level = "L0"
    profile.publish_mode = "produce_only"
    db_session.commit()
    res_l0 = await pipeline.run_pipeline(
        db_session, character_id=CID, topic="معرفی عطر رز", platform="sandbox"
    )
    assert res_l0["status"] == "library_only_L0"

    # 2. L1 (approval_required) pauses at awaiting_approval -> human approves -> publishes!
    profile.autonomy_level = "L1"
    profile.publish_mode = "approval_required"
    db_session.commit()
    res_l1 = await pipeline.run_pipeline(
        db_session,
        character_id=CID,
        topic="معرفی عطر رز با قیمت واقعی",
        platform="sandbox",
        product_sku="PRF-900",
    )
    assert res_l1["status"] == "awaiting_approval"

    app_rec = db_session.execute(
        select(ApprovalRecord).where(ApprovalRecord.content_id == res_l1["content_id"])
    ).scalar_one()
    app_rec.decision = "approved"
    app_rec.decided_at = datetime.now(UTC)
    db_session.commit()

    res_l1_done = await pipeline.run_pipeline(
        db_session,
        character_id=CID,
        topic="معرفی عطر رز با قیمت واقعی",
        platform="sandbox",
        product_sku="PRF-900",
        thread_id=res_l1["thread_id"],
        content_id=res_l1["content_id"],
    )
    assert res_l1_done["status"] == "published"
    assert res_l1_done["c2pa_verified"] is True

    # 3. Product Truth QA Gate catches hallucinated price ($999 != $50)
    bad_item = ContentItem(
        character_id=CID,
        title="Bad Price",
        caption="پیشنهاد ویژه فقط با $999 امروز!",
        script_text="قیمت غیرواقعی $999",
        ai_disclosure_applied=True,
        c2pa_verified=True,
    )
    db_session.add(bad_item)
    db_session.commit()
    qa = QualityGatePipeline(reg)
    summary = await qa.evaluate_content(
        db_session,
        profile=profile,
        content=bad_item,
        duration_sec=20.0,
        scene_cuts_count=5,
        lip_audio_overlap_pct=98.0,
        cover_image_path="",
        c2pa_verified=True,
        product_sku="PRF-900",
    )
    safety_gate = next(c for c in summary.checks if c.gate == "safety_claims")
    assert safety_gate.passed is False
    assert summary.overall_verdict in {"revise", "reject"}


@pytest.mark.asyncio
async def test_m4_20_sandbox_publishes_idempotency_quota_and_disclosure(db_session: Session) -> None:
    reg = build_default_registry()
    pub = reg.ordered_candidates("publisher")[0]

    with pytest.raises(ComplianceViolationError):
        await pub.publish(
            PublishInput(
                character_id=CID,
                content_id="c-nodisclose",
                platform="sandbox",
                media_path="dummy.mp4",
                caption="hi",
                ai_disclosure=False,
                idempotency_key="k-nodisclose",
            )
        )

    for i in range(20):
        item = ContentItem(
            character_id=CID,
            title=f"Item {i}",
            caption=f"Caption {i}",
            platform="sandbox",
        )
        db_session.add(item)
        db_session.commit()
        res = await pub.publish(
            PublishInput(
                character_id=CID,
                content_id=item.id,
                platform="sandbox",
                media_path="video.mp4",
                caption=f"Caption {i}",
                ai_disclosure=True,
                idempotency_key=f"idem-unique-{i}",
            )
        )
        assert res.data.status_code == 200
        assert res.data.ai_label_applied is True

    with pytest.raises(DuplicatePublishError):
        await pub.publish(
            PublishInput(
                character_id=CID,
                content_id="dup",
                platform="sandbox",
                media_path="video.mp4",
                caption="dup",
                ai_disclosure=True,
                idempotency_key="idem-unique-0",
            )
        )

    db_session.add(
        SocialAccount(
            character_id=CID,
            platform="tiktok",
            handle="@tt",
            daily_publish_count=25,
            daily_reset_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    with pytest.raises(QuotaExceededError):
        await pub.publish(
            PublishInput(
                character_id=CID,
                content_id="overquota",
                platform="tiktok",
                media_path="video.mp4",
                caption="over",
                ai_disclosure=True,
                idempotency_key="idem-over-quota",
            )
        )


@pytest.mark.asyncio
async def test_m5_learning_loop_direct_chat_and_20_turn_live_call(db_session: Session) -> None:
    reg = build_default_registry()

    db_session.add(
        MetricSnapshot(
            character_id=CID,
            account=f"@{CID}",
            platform="instagram",
            date=(datetime.now(UTC) - timedelta(days=1)).date().isoformat(),
            views=4500,
            likes=300,
            sales_count=0,
            revenue_usd=0.0,
        )
    )
    db_session.commit()

    mem = reg.ordered_candidates("memory")[0]
    await mem.write(
        MemoryWriteInput(
            character_id=CID,
            kind="preference",
            semantic_key="entertainment_only_pref",
            content="فقط ویدیوی سرگرم‌کننده بساز",
        )
    )
    job = LearningLoopJob(reg)
    r1 = await job.run_weekly_learning_cycle(db_session, character_id=CID)
    assert r1["discrepancy_detected"] is True
    assert r1["invalidated_memories"] == []
    await job.run_weekly_learning_cycle(db_session, character_id=CID)
    r3 = await job.run_weekly_learning_cycle(db_session, character_id=CID)
    assert len(r3["invalidated_memories"]) == 1

    mgr = LivePersonaSessionManager(reg)
    chat_res = await mgr.direct_chat(
        db_session,
        character_id=CID,
        session_id="s-f16",
        message="این ترند عطر تابستانی را بررسی کن",
    )
    assert chat_res["extracted_plan"] is not None
    assert chat_res["extracted_plan"]["task_graph"][0]["production_mode"] == "trend_motion"

    tok = mgr.create_livekit_room_token(db_session, character_id=CID)
    assert "token" in tok and tok["room_name"].startswith("hovita-live-")

    latencies: list[int] = []
    for turn in range(20):
        t0 = time.monotonic()
        lt = await mgr.handle_live_call_turn(
            db_session,
            character_id=CID,
            session_id="live-20",
            user_utterance=f"سوال زنده شماره {turn + 1}: برنامه هفته آینده چیه؟",
            end_call_and_summarize=(turn == 19),
        )
        latencies.append(int((time.monotonic() - t0) * 1000))
        if turn == 19:
            assert lt["call_summary_memory_id"] is not None

    avg_ms = sum(latencies) / len(latencies)
    assert avg_ms < 2000.0


def test_api_kill_switch_and_status_endpoints(db_session: Session) -> None:
    with TestClient(app) as client:
        h = client.get("/healthz")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"

        mods = client.get("/api/v1/modules")
        assert mods.status_code == 200
        assert len(mods.json()["modules"]) == 12

        es = client.post("/api/v1/system/emergency-stop", json={"reason": "drill"})
        assert es.status_code == 200 and es.json()["kill_switch_active"] is True

        blocked = client.post(
            "/api/v1/pipeline/produce",
            json={"topic": "blocked test", "mode": "new_idea", "platform": "sandbox"},
        )
        assert blocked.status_code == 423

        resumed = client.post("/api/v1/system/emergency-resume")
        assert resumed.status_code == 200 and resumed.json()["kill_switch_active"] is False

    clear_emergency_stop(db_session, character_id=CID)
    _ = trigger_emergency_stop
    _ = PlannerAgent
