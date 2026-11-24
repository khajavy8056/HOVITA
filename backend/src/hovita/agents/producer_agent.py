from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.agents.quality_gates import QualityGatePipeline
from hovita.config.settings import get_settings
from hovita.contracts.modules import (
    ImageGenInput,
    LLMInput,
    LLMMessage,
    MotionTransferInput,
    PublishInput,
    ResearchInput,
    SocialAnalyticsInput,
    TTSInput,
    VideoEditInput,
    VideoGenInput,
)
from hovita.contracts.registry import ProviderRegistry
from hovita.contracts.workflow import AUTONOMY_TO_PUBLISH_MODE, PRODUCTION_STAGES
from hovita.db.models import (
    ApprovalRecord,
    ContentItem,
    IdentityAsset,
    MetricSnapshot,
    ProductItem,
    StageCheckpoint,
    VoiceAsset,
)
from hovita.db.repositories import assert_single_persona_id
from hovita.economy.budget import BudgetPolicy
from hovita.modules.llm.router import run_llm_with_failover
from hovita.security.audit import append_audit_log
from hovita.security.emergency import assert_not_emergency_stopped


class SimulatedStageCrashError(RuntimeError):
    """Injected fault used in resilience drills to test checkpoint resume without re-running prior stages."""


class ProducerPipeline:
    """Agent #2 + 11-Stage Checkpointed Production & Publishing Pipeline (Section 35).

    Stages:
      01_idea_research -> 02_script_variants -> 03_image_gen -> 04_video_motion ->
      05_voice_tts -> 06_post_process_c2pa -> 07_quality_gates -> 08_human_approval ->
      09_schedule -> 10_publish -> 11_analytics
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry
        self.qa = QualityGatePipeline(registry)

    def _get_completed_checkpoints(
        self, session: Session, thread_id: str
    ) -> dict[str, StageCheckpoint]:
        rows = list(
            session.execute(
                select(StageCheckpoint).where(StageCheckpoint.thread_id == thread_id)
            ).scalars()
        )
        return {r.stage_name: r for r in rows}

    def _save_stage_checkpoint(
        self,
        session: Session,
        *,
        character_id: str,
        thread_id: str,
        content_id: str,
        stage_name: str,
        stage_order: int,
        status: str,
        input_json: dict[str, Any],
        output_json: dict[str, Any],
        error: str | None = None,
    ) -> StageCheckpoint:
        row = session.execute(
            select(StageCheckpoint).where(
                StageCheckpoint.thread_id == thread_id,
                StageCheckpoint.stage_name == stage_name,
            )
        ).scalar_one_or_none()
        if row is None:
            row = StageCheckpoint(
                character_id=character_id,
                thread_id=thread_id,
                content_id=content_id,
                stage_name=stage_name,
                stage_order=stage_order,
                status=status,
                input_json=input_json,
                output_json=output_json,
                error=error,
            )
            session.add(row)
        else:
            row.status = status
            row.input_json = input_json
            row.output_json = output_json
            row.error = error
        session.commit()
        session.refresh(row)
        return row

    async def run_pipeline(
        self,
        session: Session,
        *,
        character_id: str,
        topic: str,
        mode: str = "new",
        platform: str = "sandbox",
        product_sku: str | None = None,
        sensitive_content: bool = False,
        thread_id: str | None = None,
        content_id: str | None = None,
        inject_crash_at_stage: str | None = None,
        executed_stages_log: list[str] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        profile = assert_single_persona_id(session, character_id)
        assert_not_emergency_stopped(session, "producer_pipeline")

        tid = thread_id or f"prod_{uuid.uuid4().hex[:12]}"
        if content_id:
            content = session.execute(
                select(ContentItem).where(ContentItem.id == content_id)
            ).scalar_one()
        else:
            content = ContentItem(
                character_id=character_id,
                type="reel_9_16",
                mode="trend" if mode == "trend_motion" else ("hybrid" if mode == "hybrid" else "new"),
                status="draft",
                thread_id=tid,
                title=topic[:250],
                platform=platform,
                sensitive_content=sensitive_content,
                ai_disclosure_applied=True,
                assets={},
                hashtags=["#Hovita", "#AI"],
            )
            session.add(content)
            session.commit()
            session.refresh(content)

        checkpoints = self._get_completed_checkpoints(session, tid)
        trace_log = executed_stages_log if executed_stages_log is not None else []

        def _already_done(stage: str) -> dict[str, Any] | None:
            cp = checkpoints.get(stage)
            if cp and cp.status == "completed":
                return dict(cp.output_json)
            return None

        # --- Stage 01: Idea & Research ---
        s01_out = _already_done("01_idea_research")
        if s01_out is None:
            if inject_crash_at_stage == "01_idea_research":
                raise SimulatedStageCrashError("Simulated crash at 01_idea_research")
            trace_log.append("01_idea_research")
            researcher = self.registry.ordered_candidates("research")[0]
            res_out = await researcher.search_and_extract(ResearchInput(query=topic, max_results=3))
            s01_out = {"summary": res_out.data.summary, "source_count": len(res_out.data.sources)}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="01_idea_research",
                stage_order=1,
                status="completed",
                input_json={"topic": topic},
                output_json=s01_out,
            )

        # --- Stage 02: Script & 3 Caption Variants ---
        s02_out = _already_done("02_script_variants")
        if s02_out is None:
            if inject_crash_at_stage == "02_script_variants":
                raise SimulatedStageCrashError("Simulated crash at 02_script_variants")
            trace_log.append("02_script_variants")
            prod_truth = ""
            if product_sku:
                prod_row = session.execute(
                    select(ProductItem).where(
                        ProductItem.character_id == character_id, ProductItem.sku == product_sku
                    )
                ).scalar_one_or_none()
                if prod_row:
                    prod_truth = (
                        f"Product truth: {prod_row.name}, price=${prod_row.price}, "
                        f"discount=${prod_row.discount}, buy={prod_row.buy_link}"
                    )
            llm_res = await run_llm_with_failover(
                self.registry,
                LLMInput(
                    messages=[
                        LLMMessage(
                            role="system",
                            content=f"Persona: {profile.name} | Job: {profile.job} | Tone: {profile.tone} | Dont: {profile.dont}",
                        ),
                        LLMMessage(
                            role="user",
                            content=f"Write a short Farsi 3s hook + script + caption about: {topic}. {prod_truth}",
                        ),
                    ],
                    json_schema={
                        "type": "object",
                        "properties": {
                            "hook_3s": {"type": "string"},
                            "script": {"type": "string"},
                            "captions": {"type": "array"},
                        },
                        "required": ["hook_3s", "script", "captions"],
                    },
                    task_complexity="medium",
                ),
                session=session,
                character_id=character_id,
                pipeline="stage_02_writer",
                content_id=content.id,
            )
            st = llm_res.data.structured or {}
            hook = str(st.get("hook_3s") or f"آیا می‌دانید درباره {topic} چه نکته‌ای پنهان مانده؟")
            script = str(st.get("script") or f"{hook}\n{topic}\n{prod_truth}")
            content.script_text = f"{hook}\n{script}".strip()
            content.caption = f"{hook} — {topic} {prod_truth}".strip()
            session.commit()
            s02_out = {"hook_3s": hook, "script_len": len(content.script_text)}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="02_script_variants",
                stage_order=2,
                status="completed",
                input_json=s01_out,
                output_json=s02_out,
            )

        # --- Stage 03: Image Keyframe Generation ---
        s03_out = _already_done("03_image_gen")
        if s03_out is None:
            if inject_crash_at_stage == "03_image_gen":
                raise SimulatedStageCrashError("Simulated crash at 03_image_gen")
            trace_log.append("03_image_gen")
            ref_img = session.execute(
                select(IdentityAsset)
                .where(IdentityAsset.character_id == character_id, IdentityAsset.kind == "ref_image")
                .order_by(IdentityAsset.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            lora_asset = session.execute(
                select(IdentityAsset)
                .where(IdentityAsset.character_id == character_id, IdentityAsset.kind == "lora")
                .order_by(IdentityAsset.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            img_provider = self.registry.ordered_candidates("image_gen")[0]
            img_res = await img_provider.generate_image(
                ImageGenInput(
                    character_id=character_id,
                    prompt=f"Keyframe for reel: {topic}",
                    locked_physical_prompt=profile.locked_physical_prompt,
                    angle="front",
                    reference_image_paths=[ref_img.path] if ref_img else [],
                    lora_path=lora_asset.path if lora_asset else None,
                )
            )
            s03_out = {"keyframe_path": img_res.data.file_path, "arcface": img_res.data.arcface_score}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="03_image_gen",
                stage_order=3,
                status="completed",
                input_json=s02_out,
                output_json=s03_out,
            )

        # --- Stage 04: Video / Motion Transfer ---
        s04_out = _already_done("04_video_motion")
        if s04_out is None:
            if inject_crash_at_stage == "04_video_motion":
                self._save_stage_checkpoint(
                    session,
                    character_id=character_id,
                    thread_id=tid,
                    content_id=content.id,
                    stage_name="04_video_motion",
                    stage_order=4,
                    status="failed",
                    input_json=s03_out,
                    output_json={},
                    error="Simulated crash at 04_video_motion",
                )
                raise SimulatedStageCrashError("Simulated crash at 04_video_motion")
            trace_log.append("04_video_motion")
            if mode == "trend_motion":
                mt = self.registry.ordered_candidates("motion_transfer")[0]
                v_res = await mt.transfer_motion(
                    MotionTransferInput(
                        character_id=character_id,
                        character_image_path=str(s03_out["keyframe_path"]),
                        reference_video_path=str(s03_out["keyframe_path"]),
                    )
                )
                raw_video_path = v_res.data.file_path
            else:
                vg = self.registry.ordered_candidates("video_gen")[0]
                v_res2 = await vg.generate_video(
                    VideoGenInput(
                        character_id=character_id,
                        prompt=topic,
                        keyframe_image_path=str(s03_out["keyframe_path"]),
                        duration_sec=18,
                    )
                )
                raw_video_path = v_res2.data.file_path
            s04_out = {"raw_video_path": raw_video_path}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="04_video_motion",
                stage_order=4,
                status="completed",
                input_json=s03_out,
                output_json=s04_out,
            )

        # --- Stage 05: Voice / TTS ---
        s05_out = _already_done("05_voice_tts")
        if s05_out is None:
            if inject_crash_at_stage == "05_voice_tts":
                raise SimulatedStageCrashError("Simulated crash at 05_voice_tts")
            trace_log.append("05_voice_tts")
            voice = session.execute(
                select(VoiceAsset)
                .where(VoiceAsset.character_id == character_id)
                .order_by(VoiceAsset.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            tts = self.registry.ordered_candidates("tts")[0]
            t_res = await tts.synthesize(
                TTSInput(
                    character_id=character_id,
                    text=content.script_text or topic,
                    voice_id=voice.voice_id if voice else f"voice_{character_id}_fa_balanced",
                    language="fa",
                )
            )
            s05_out = {"audio_path": t_res.data.file_path, "voice_id": t_res.data.voice_id}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="05_voice_tts",
                stage_order=5,
                status="completed",
                input_json=s04_out,
                output_json=s05_out,
            )

        # --- Stage 06: FFmpeg Post-Process & C2PA Metadata ---
        s06_out = _already_done("06_post_process_c2pa")
        if s06_out is None:
            if inject_crash_at_stage == "06_post_process_c2pa":
                raise SimulatedStageCrashError("Simulated crash at 06_post_process_c2pa")
            trace_log.append("06_post_process_c2pa")
            editor = self.registry.ordered_candidates("video_edit")[0]
            e_res = await editor.compose(
                VideoEditInput(
                    character_id=character_id,
                    video_paths=[str(s04_out["raw_video_path"])],
                    audio_path=str(s05_out["audio_path"]),
                    target_duration_sec=18,
                    inject_c2pa=True,
                )
            )
            s06_out = e_res.data.model_dump()
            content.assets = {
                "keyframe": s03_out["keyframe_path"],
                "audio": s05_out["audio_path"],
                "final_mp4": s06_out["output_mp4_path"],
                "cover_png": s06_out["cover_image_path"],
            }
            content.c2pa_verified = bool(s06_out["c2pa_verified"])
            content.ai_disclosure_applied = True
            content.status = "qa"
            session.commit()
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="06_post_process_c2pa",
                stage_order=6,
                status="completed",
                input_json=s05_out,
                output_json=s06_out,
            )

        # --- Stage 07: 7 Quality Gates (max 2 revisions) ---
        s07_out = _already_done("07_quality_gates")
        if s07_out is None:
            trace_log.append("07_quality_gates")
            budget_policy = BudgetPolicy()
            while True:
                qa_summary = await self.qa.evaluate_content(
                    session,
                    profile=profile,
                    content=content,
                    duration_sec=float(s06_out["duration_sec"]),
                    scene_cuts_count=int(s06_out["scene_cuts_count"]),
                    lip_audio_overlap_pct=float(s06_out["lip_audio_overlap_pct"]),
                    cover_image_path=str(s03_out["keyframe_path"]),
                    c2pa_verified=bool(s06_out["c2pa_verified"]),
                    product_sku=product_sku,
                )
                if qa_summary.overall_verdict == "pass":
                    break
                if qa_summary.overall_verdict == "revise" and budget_policy.can_revise_content(
                    content.revision_count
                ):
                    content.revision_count += 1
                    # Apply targeted auto-remediation on failed gates
                    content.caption = f"آیا می‌دانستید این نکته نتیجه را تغییر می‌دهد؟ {topic}"
                    content.script_text = f"{content.caption}\n{topic}"
                    content.ai_disclosure_applied = True
                    session.commit()
                    continue
                content.status = "rejected"
                session.commit()
                self._save_stage_checkpoint(
                    session,
                    character_id=character_id,
                    thread_id=tid,
                    content_id=content.id,
                    stage_name="07_quality_gates",
                    stage_order=7,
                    status="failed",
                    input_json=s06_out,
                    output_json=qa_summary.model_dump(mode="json"),
                    error="Quality gates rejected after max revisions",
                )
                return {
                    "thread_id": tid,
                    "content_id": content.id,
                    "status": "rejected",
                    "executed_stages": trace_log,
                    "qa": qa_summary.model_dump(mode="json"),
                }
            s07_out = qa_summary.model_dump(mode="json")
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="07_quality_gates",
                stage_order=7,
                status="completed",
                input_json=s06_out,
                output_json=s07_out,
            )

        # --- Stage 08: Human Approval / Autonomy Level L0-L3 ---
        s08_out = _already_done("08_human_approval")
        publish_mode = AUTONOMY_TO_PUBLISH_MODE.get(profile.autonomy_level, "approval_required")
        if s08_out is None:
            trace_log.append("08_human_approval")
            if publish_mode == "produce_only" or profile.autonomy_level == "L0":
                content.status = "draft"
                session.commit()
                self._save_stage_checkpoint(
                    session,
                    character_id=character_id,
                    thread_id=tid,
                    content_id=content.id,
                    stage_name="08_human_approval",
                    stage_order=8,
                    status="paused",
                    input_json=s07_out,
                    output_json={"mode": "L0_produce_only", "publish_allowed": False},
                )
                return {
                    "thread_id": tid,
                    "content_id": content.id,
                    "status": "library_only_L0",
                    "executed_stages": trace_log,
                }

            if profile.autonomy_level == "L1" or content.sensitive_content:
                existing_app = session.execute(
                    select(ApprovalRecord).where(ApprovalRecord.content_id == content.id)
                ).scalar_one_or_none()
                if existing_app is None or existing_app.decision != "approved":
                    if existing_app is None:
                        session.add(
                            ApprovalRecord(
                                character_id=character_id,
                                content_id=content.id,
                                level=profile.autonomy_level,
                                actor="human",
                                decision="pending",
                                expires_at=datetime.now(UTC) + timedelta(hours=24),
                            )
                        )
                    content.status = "awaiting_approval"
                    session.commit()
                    self._save_stage_checkpoint(
                        session,
                        character_id=character_id,
                        thread_id=tid,
                        content_id=content.id,
                        stage_name="08_human_approval",
                        stage_order=8,
                        status="paused",
                        input_json=s07_out,
                        output_json={"waiting_human_approval": True},
                    )
                    return {
                        "thread_id": tid,
                        "content_id": content.id,
                        "status": "awaiting_approval",
                        "executed_stages": trace_log,
                    }
            else:
                # L2 / L3 Autonomous non-sensitive auto-approval
                session.add(
                    ApprovalRecord(
                        character_id=character_id,
                        content_id=content.id,
                        level=profile.autonomy_level,
                        actor="auto",
                        decision="approved",
                        decided_at=datetime.now(UTC),
                        comment=f"Auto-approved under {profile.autonomy_level}",
                    )
                )
                session.commit()
            s08_out = {"approved": True, "level": profile.autonomy_level}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="08_human_approval",
                stage_order=8,
                status="completed",
                input_json=s07_out,
                output_json=s08_out,
            )

        # --- Stage 09: Schedule ---
        s09_out = _already_done("09_schedule")
        if s09_out is None:
            trace_log.append("09_schedule")
            publish_at = datetime.now(UTC)
            content.status = "scheduled"
            content.publish_at = publish_at
            session.commit()
            s09_out = {"publish_at": publish_at.isoformat()}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="09_schedule",
                stage_order=9,
                status="completed",
                input_json=s08_out,
                output_json=s09_out,
            )

        # --- Stage 10: Official Publish with Idempotency Key ---
        s10_out = _already_done("10_publish")
        if s10_out is None:
            trace_log.append("10_publish")
            assert_not_emergency_stopped(session, "stage_10_publish")
            file_hash = str(s06_out["file_sha256"])
            date_str = datetime.now(UTC).date().isoformat()
            idem_key = hashlib.sha256(
                f"{file_hash}:{platform}:{date_str}:{content.id}".encode()
            ).hexdigest()
            pub_provider = self.registry.ordered_candidates("publisher")[0]
            pub_res = await pub_provider.publish(
                PublishInput(
                    character_id=character_id,
                    content_id=content.id,
                    platform=platform,  # type: ignore[arg-type]
                    media_path=str(s06_out["output_mp4_path"]),
                    cover_path=str(s06_out["cover_image_path"]),
                    caption=content.caption,
                    hashtags=list(content.hashtags),
                    ai_disclosure=True,
                    idempotency_key=idem_key,
                )
            )
            s10_out = pub_res.data.model_dump()
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="10_publish",
                stage_order=10,
                status="completed",
                input_json=s09_out,
                output_json=s10_out,
            )
            append_audit_log(
                session,
                character_id=character_id,
                actor="producer_pipeline",
                action="content_published",
                resource_type="content_item",
                resource_id=content.id,
                details={"platform": platform, "published_id": s10_out["published_id"]},
            )

        # --- Stage 11: Analytics Snapshot ---
        s11_out = _already_done("11_analytics")
        if s11_out is None:
            trace_log.append("11_analytics")
            analytics_p = self.registry.ordered_candidates("social_analytics")[0]
            a_res = await analytics_p.fetch(
                SocialAnalyticsInput(
                    character_id=character_id,
                    platform="instagram" if platform == "sandbox" else platform,  # type: ignore[arg-type]
                    account_handle=f"@{character_id}",
                    action="post_insights",
                    post_ids=[str(s10_out["published_id"])],
                )
            )
            for m in a_res.data.metrics:
                session.add(
                    MetricSnapshot(
                        character_id=character_id,
                        content_id=content.id,
                        account=f"@{character_id}",
                        platform=platform,
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
            session.commit()
            s11_out = {"metrics_recorded": len(a_res.data.metrics)}
            self._save_stage_checkpoint(
                session,
                character_id=character_id,
                thread_id=tid,
                content_id=content.id,
                stage_name="11_analytics",
                stage_order=11,
                status="completed",
                input_json=s10_out,
                output_json=s11_out,
            )

        _ = settings
        return {
            "thread_id": tid,
            "content_id": content.id,
            "status": "published",
            "published_id": s10_out["published_id"],
            "c2pa_verified": s06_out["c2pa_verified"],
            "executed_stages": trace_log,
            "total_stages": len(PRODUCTION_STAGES),
        }
