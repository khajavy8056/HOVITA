from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hovita.agents.identity_studio import IdentityStudio
from hovita.agents.learning_job import LearningLoopJob
from hovita.agents.live_session import LivePersonaSessionManager
from hovita.agents.onboarding import (
    ConversationalPersonaBuilder,
    evaluate_job_kpi,
    generate_behavioral_style_samples,
)
from hovita.agents.planner_agent import PlannerAgent
from hovita.agents.producer_agent import ProducerPipeline
from hovita.agents.social_connect import SocialConnector
from hovita.config.settings import get_settings
from hovita.contracts.workflow import AUTONOMY_TO_PUBLISH_MODE
from hovita.db.base import get_session_factory, init_db_schema
from hovita.db.models import (
    ApprovalRecord,
    AuditLog,
    CharacterProfile,
    ContentItem,
    DecisionLog,
    EnvironmentAsset,
    IdentityAsset,
    JobRun,
    OnboardingProgress,
    ProductItem,
    QAResult,
    SocialAccount,
    VoiceAsset,
)
from hovita.db.repositories import seed_installation
from hovita.economy.budget import BudgetPolicy
from hovita.economy.ledger import get_daily_spend_usd, get_monthly_spend_usd
from hovita.modules.bootstrap import build_default_registry
from hovita.resources.gpu_manager import GPUManager
from hovita.security.audit import append_audit_log, verify_audit_chain
from hovita.security.emergency import (
    clear_emergency_stop,
    is_kill_switch_active,
    trigger_emergency_stop,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    init_db_schema()
    with get_session_factory()() as session:
        seed_installation(session)
    app.state.registry = build_default_registry()
    app.state.gpu = GPUManager()
    yield


app = FastAPI(
    title="HOVITA Autonomous Intelligent Persona API",
    version="1.0.0",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():  # type: ignore[no-untyped-def]
    factory = get_session_factory()
    with factory() as session:
        yield session


def _get_registry():  # type: ignore[no-untyped-def]
    if not hasattr(app.state, "registry"):
        app.state.registry = build_default_registry()
    return app.state.registry


def _get_gpu() -> GPUManager:
    if not hasattr(app.state, "gpu"):
        app.state.gpu = GPUManager()
    return app.state.gpu


# --- Request DTOs ---


class BuilderTurnRequest(BaseModel):
    thread_id: str = "default-onboarding"
    message: str = Field(..., min_length=1)
    explicit_fields: dict[str, Any] = Field(default_factory=dict)


class AutonomyChangeRequest(BaseModel):
    autonomy_level: Literal["L0", "L1", "L2", "L3"]


class EmergencyToggleRequest(BaseModel):
    reason: str = "Emergency stop triggered from dashboard"


class MultiAngleRequest(BaseModel):
    refinement_note: str = ""
    seed: int = 42
    approve: bool = True


class VoiceAuditionRequest(BaseModel):
    language: Literal["fa", "en"] = "fa"
    selected_energy: Literal["calm", "balanced", "energetic"] = "balanced"
    approve: bool = True


class EnvironmentRequest(BaseModel):
    kind: Literal["real", "virtual"] = "real"
    capture_method: Literal["walkthrough_video", "multi_angle_photos", "chat", "virtual_ai"] = "multi_angle_photos"
    media: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    fixed_props: list[str] = Field(default_factory=list)
    product_zones: list[str] = Field(default_factory=list)
    approve: bool = True


class ProductUpsertRequest(BaseModel):
    sku: str
    name: str
    brand: str = ""
    price: float
    discount: float = 0.0
    stock: int = 10
    features: dict[str, Any] = Field(default_factory=dict)
    buy_link: str = ""


class SocialConnectRequest(BaseModel):
    platform: Literal["instagram", "tiktok", "youtube"]
    handle: str
    access_token: str = "sandbox_oauth_token"
    expires_in_days: int = 60


class PageAuditRequest(BaseModel):
    platform: Literal["instagram", "tiktok", "youtube"] = "instagram"
    handle: str = "@hovita_persona"
    page_state: Literal["fresh", "existing"] = "existing"


class PlanRequest(BaseModel):
    preferred_platform: str = "sandbox"
    user_directive: str = ""


class ProduceRequest(BaseModel):
    topic: str
    mode: Literal["trend_motion", "new_idea", "hybrid"] = "new_idea"
    platform: Literal["instagram", "tiktok", "youtube", "sandbox"] = "sandbox"
    product_sku: str | None = None
    sensitive_content: bool = False
    thread_id: str | None = None
    content_id: str | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""
    edited_caption: str | None = None


class ChatRequest(BaseModel):
    session_id: str = "manager-chat-1"
    message: str
    with_voice: bool = False
    attachments: list[dict[str, str]] = Field(default_factory=list)


class LiveCallTurnRequest(BaseModel):
    session_id: str = "live-call-1"
    utterance: str
    end_call_and_summarize: bool = False


# --- Endpoints ---


@app.get("/healthz")
@app.get("/health")
def healthz(session: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    row = session.execute(select(CharacterProfile).limit(1)).scalar_one_or_none()
    return {
        "status": "ok",
        "version": "1.0.0",
        "character_id": row.id if row else settings.character_id,
        "autonomy_level": row.autonomy_level if row else "L1",
        "emergency_stop": is_kill_switch_active(session),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/api/v1/system/status")
def system_status(session: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    budget = BudgetPolicy().evaluate(session, profile.id)
    gpu_state = _get_gpu().status(session)
    audit_ok, audit_count_or_err = verify_audit_chain(session)
    pending_count = len(
        list(
            session.execute(
                select(ApprovalRecord).where(
                    ApprovalRecord.character_id == profile.id,
                    ApprovalRecord.decision == "pending",
                )
            ).scalars()
        )
    )
    return {
        "character": {
            "id": profile.id,
            "name": profile.name,
            "job": profile.job,
            "goal": profile.goal,
            "status": profile.status,
            "autonomy_level": profile.autonomy_level,
            "publish_mode": profile.publish_mode,
            "emergency_stop": is_kill_switch_active(session),
            "version": profile.version,
        },
        "budget": budget.model_dump(),
        "gpu": gpu_state.model_dump(mode="json"),
        "pending_approvals": pending_count,
        "audit_chain_valid": audit_ok,
        "audit_chain_seq": audit_count_or_err,
        "free_first_policy": settings.brain_model_gate,
    }


@app.post("/api/v1/system/emergency-stop")
def api_emergency_stop(
    body: EmergencyToggleRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    return trigger_emergency_stop(
        session, character_id=profile.id, actor="human_dashboard", reason=body.reason
    )


@app.post("/api/v1/system/emergency-resume")
def api_emergency_resume(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    cleared = clear_emergency_stop(session, character_id=profile.id, actor="human_dashboard")
    return {"kill_switch_active": False, "cleared": cleared}


@app.post("/api/v1/system/autonomy")
def api_set_autonomy(
    body: AutonomyChangeRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    profile.autonomy_level = body.autonomy_level
    profile.publish_mode = AUTONOMY_TO_PUBLISH_MODE[body.autonomy_level]
    session.commit()
    append_audit_log(
        session,
        character_id=profile.id,
        actor="human_dashboard",
        action="autonomy_level_changed",
        resource_type="character_profile",
        resource_id=profile.id,
        details={"autonomy_level": profile.autonomy_level, "publish_mode": profile.publish_mode},
    )
    return {"autonomy_level": profile.autonomy_level, "publish_mode": profile.publish_mode}


@app.get("/api/v1/modules")
def api_list_modules() -> dict[str, Any]:
    reg = _get_registry()
    return {mod: [m.model_dump(mode="json") for m in metas] for mod, metas in reg.list_providers().items()}


@app.get("/api/v1/persona")
def api_get_persona(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    id_assets = list(
        session.execute(
            select(IdentityAsset).where(IdentityAsset.character_id == profile.id)
        ).scalars()
    )
    voices = list(
        session.execute(select(VoiceAsset).where(VoiceAsset.character_id == profile.id)).scalars()
    )
    envs = list(
        session.execute(
            select(EnvironmentAsset).where(EnvironmentAsset.character_id == profile.id)
        ).scalars()
    )
    return {
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "status": profile.status,
            "job": profile.job,
            "goal": profile.goal,
            "kpis": profile.kpis,
            "audience": profile.audience,
            "appearance": profile.appearance,
            "locked_physical_prompt": profile.locked_physical_prompt,
            "tone": profile.tone,
            "do": profile.do,
            "dont": profile.dont,
            "autonomy_level": profile.autonomy_level,
            "publish_mode": profile.publish_mode,
            "version": profile.version,
        },
        "identity_assets": [
            {
                "id": a.id,
                "kind": a.kind,
                "angle": a.angle,
                "path": a.path,
                "arcface_score": a.arcface_score,
                "approved": a.approved_at is not None,
            }
            for a in id_assets
        ],
        "voices": [
            {
                "id": v.id,
                "voice_id": v.voice_id,
                "engine": v.engine,
                "language": v.language,
                "sample": v.ref_sample,
            }
            for v in voices
        ],
        "environments": [
            {"id": e.id, "kind": e.kind, "palette": e.palette, "approved_version": e.approved_version}
            for e in envs
        ],
    }


@app.post("/api/v1/persona/builder")
def api_persona_builder(
    body: BuilderTurnRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    builder = ConversationalPersonaBuilder()
    res = builder.process_turn(
        session,
        character_id=profile.id,
        thread_id=body.thread_id,
        user_message=body.message,
        explicit_fields=body.explicit_fields,
    )
    return res.model_dump(mode="json")


@app.get("/api/v1/persona/behavior-samples")
def api_behavior_samples(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    samples = generate_behavioral_style_samples(profile)
    kpi_eval = evaluate_job_kpi(
        profile.job, {"views": 4200, "followers_delta": 50, "sales_count": 3, "revenue_usd": 120.0}
    )
    return {"samples": samples, "kpi_evaluation": kpi_eval}


@app.post("/api/v1/identity/multi-angle")
async def api_identity_multi_angle(
    body: MultiAngleRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    studio = IdentityStudio(_get_registry())
    return await studio.generate_multi_angle_identity(
        session,
        character_id=profile.id,
        refinement_note=body.refinement_note,
        seed=body.seed,
        approve=body.approve,
    )


@app.post("/api/v1/identity/voice-audition")
async def api_voice_audition(
    body: VoiceAuditionRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    studio = IdentityStudio(_get_registry())
    return await studio.audition_and_lock_voice(
        session,
        character_id=profile.id,
        language=body.language,
        selected_energy=body.selected_energy,
        approve=body.approve,
    )


@app.post("/api/v1/identity/intro-preview")
async def api_intro_preview(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    studio = IdentityStudio(_get_registry())
    return await studio.generate_intro_preview_video(session, character_id=profile.id)


@app.post("/api/v1/identity/environment")
def api_register_environment(
    body: EnvironmentRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    studio = IdentityStudio(_get_registry())
    env = studio.register_environment_pack(
        session,
        character_id=profile.id,
        kind=body.kind,
        capture_method=body.capture_method,
        media=body.media,
        palette=body.palette,
        fixed_props=body.fixed_props,
        product_zones=body.product_zones,
        approve=body.approve,
    )
    return {
        "id": env.id,
        "kind": env.kind,
        "palette": env.palette,
        "approved_version": env.approved_version,
    }


@app.get("/api/v1/products")
def api_list_products(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    rows = list(
        session.execute(
            select(ProductItem).where(ProductItem.character_id == profile.id)
        ).scalars()
    )
    return {
        "products": [
            {
                "id": p.id,
                "sku": p.sku,
                "name": p.name,
                "brand": p.brand,
                "price": p.price,
                "discount": p.discount,
                "stock": p.stock,
                "status": p.status,
                "buy_link": p.buy_link,
                "version": p.version,
                "source": p.source,
            }
            for p in rows
        ]
    }


@app.post("/api/v1/products")
def api_upsert_product(
    body: ProductUpsertRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    studio = IdentityStudio(_get_registry())
    p = studio.upsert_product(
        session,
        character_id=profile.id,
        sku=body.sku,
        name=body.name,
        brand=body.brand,
        price=body.price,
        discount=body.discount,
        stock=body.stock,
        features=body.features,
        buy_link=body.buy_link,
    )
    return {"id": p.id, "sku": p.sku, "price": p.price, "version": p.version}


@app.post("/api/v1/products/import")
async def api_import_products(
    file: UploadFile = File(...), session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    raw = await file.read()
    studio = IdentityStudio(_get_registry())
    items = studio.import_products_csv_or_xlsx(
        session,
        character_id=profile.id,
        filename=file.filename or "products.csv",
        raw_bytes=raw,
    )
    return {"imported_count": len(items), "skus": [i.sku for i in items]}


@app.post("/api/v1/social/connect")
def api_connect_social(
    body: SocialConnectRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    conn = SocialConnector(_get_registry())
    return conn.connect_account(
        session,
        character_id=profile.id,
        platform=body.platform,
        handle=body.handle,
        access_token=body.access_token,
        expires_in_days=body.expires_in_days,
    )


@app.get("/api/v1/social/accounts")
def api_social_accounts(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    conn = SocialConnector(_get_registry())
    rows = list(
        session.execute(
            select(SocialAccount).where(SocialAccount.character_id == profile.id)
        ).scalars()
    )
    return {"accounts": [conn.account_status(r) for r in rows]}


@app.post("/api/v1/social/audit")
async def api_social_audit(
    body: PageAuditRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    conn = SocialConnector(_get_registry())
    return await conn.audit_page_and_run_setup_wizard(
        session,
        character_id=profile.id,
        platform=body.platform,
        handle=body.handle,
        page_state=body.page_state,
    )


@app.get("/api/v1/onboarding/steps")
def api_onboarding_steps(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    steps = list(
        session.execute(
            select(OnboardingProgress)
            .where(OnboardingProgress.character_id == profile.id)
            .order_by(OnboardingProgress.step_index.asc())
        ).scalars()
    )
    return {
        "steps": [
            {
                "step_index": s.step_index,
                "step_key": s.step_key,
                "title": s.title,
                "status": s.status,
                "pct": s.pct,
                "output": s.output_json,
            }
            for s in steps
        ]
    }


@app.post("/api/v1/planner/plan")
async def api_planner_plan(
    body: PlanRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    planner = PlannerAgent(_get_registry())
    return await planner.plan_next_content(
        session,
        character_id=profile.id,
        thread_id=f"api_plan_{uuid.uuid4().hex[:8]}",
        preferred_platform=body.preferred_platform,
        user_directive=body.user_directive,
    )


@app.post("/api/v1/pipeline/produce")
async def api_pipeline_produce(
    body: ProduceRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    if is_kill_switch_active(session):
        raise HTTPException(status_code=423, detail="Emergency kill switch active")
    pipeline = ProducerPipeline(_get_registry())
    return await pipeline.run_pipeline(
        session,
        character_id=profile.id,
        topic=body.topic,
        mode=body.mode,
        platform=body.platform,
        product_sku=body.product_sku,
        sensitive_content=body.sensitive_content,
        thread_id=body.thread_id,
        content_id=body.content_id,
    )


@app.get("/api/v1/content")
def api_list_content(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    items = list(
        session.execute(
            select(ContentItem)
            .where(ContentItem.character_id == profile.id)
            .order_by(ContentItem.created_at.desc())
        ).scalars()
    )
    out = []
    for item in items:
        qas = list(
            session.execute(select(QAResult).where(QAResult.content_id == item.id)).scalars()
        )
        out.append(
            {
                "id": item.id,
                "title": item.title,
                "mode": item.mode,
                "status": item.status,
                "platform": item.platform,
                "caption": item.caption,
                "assets": item.assets,
                "published_id": item.published_id,
                "c2pa_verified": item.c2pa_verified,
                "ai_disclosure_applied": item.ai_disclosure_applied,
                "revision_count": item.revision_count,
                "created_at": item.created_at.isoformat(),
                "qa_gates": [
                    {
                        "gate": q.gate,
                        "score": q.score,
                        "threshold": q.threshold,
                        "verdict": q.verdict,
                        "note": q.note,
                    }
                    for q in qas
                ],
            }
        )
    return {"items": out}


@app.get("/api/v1/approvals/pending")
def api_pending_approvals(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    rows = list(
        session.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.character_id == profile.id,
                ApprovalRecord.decision == "pending",
            )
        ).scalars()
    )
    items = []
    for a in rows:
        content = session.execute(
            select(ContentItem).where(ContentItem.id == a.content_id)
        ).scalar_one_or_none()
        items.append(
            {
                "approval_id": a.id,
                "content_id": a.content_id,
                "level": a.level,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                "title": content.title if content else "",
                "caption": content.caption if content else "",
                "platform": content.platform if content else "",
                "assets": content.assets if content else {},
            }
        )
    return {"pending": items}


@app.post("/api/v1/approvals/{content_id}/decide")
async def api_decide_approval(
    content_id: str, body: ApprovalDecisionRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    app_rec = session.execute(
        select(ApprovalRecord).where(
            ApprovalRecord.character_id == profile.id,
            ApprovalRecord.content_id == content_id,
        )
    ).scalar_one_or_none()
    content = session.execute(
        select(ContentItem).where(ContentItem.id == content_id)
    ).scalar_one_or_none()
    if app_rec is None or content is None:
        raise HTTPException(status_code=404, detail="Approval record or content not found")

    if body.edited_caption is not None:
        content.caption = body.edited_caption
    app_rec.decision = body.decision
    app_rec.comment = body.comment
    app_rec.decided_at = datetime.now(UTC)

    if body.decision == "rejected":
        content.status = "rejected"
        session.commit()
        return {"content_id": content.id, "status": "rejected"}

    session.commit()
    pipeline = ProducerPipeline(_get_registry())
    return await pipeline.run_pipeline(
        session,
        character_id=profile.id,
        topic=content.title,
        mode=content.mode,
        platform=content.platform,
        thread_id=content.thread_id,
        content_id=content.id,
    )


@app.post("/api/v1/learning/weekly")
async def api_learning_weekly(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    job = LearningLoopJob(_get_registry())
    return await job.run_weekly_learning_cycle(session, character_id=profile.id)


@app.get("/api/v1/costs/summary")
def api_costs_summary(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    budget = BudgetPolicy().evaluate(session, profile.id)
    daily_usd = get_daily_spend_usd(session, profile.id)
    monthly_usd = get_monthly_spend_usd(session, profile.id)
    rows = list(
        session.execute(
            select(
                JobRun.provider_id,
                func.count(JobRun.id),
                func.coalesce(func.sum(JobRun.tokens_in), 0),
                func.coalesce(func.sum(JobRun.tokens_out), 0),
                func.coalesce(func.sum(JobRun.cost_usd), 0.0),
                func.coalesce(func.sum(JobRun.gpu_min), 0.0),
                func.coalesce(func.sum(JobRun.energy_kwh), 0.0),
            )
            .where(JobRun.character_id == profile.id)
            .group_by(JobRun.provider_id)
        ).all()
    )
    return {
        "daily_budget": budget.model_dump(),
        "daily_spend_usd": daily_usd,
        "monthly_spend_usd": monthly_usd,
        "by_provider": [
            {
                "provider_id": str(r[0]),
                "runs": int(r[1]),
                "tokens_in": int(r[2]),
                "tokens_out": int(r[3]),
                "cost_usd": round(float(r[4]), 6),
                "gpu_min": round(float(r[5]), 3),
                "energy_kwh": round(float(r[6]), 6),
            }
            for r in rows
        ],
    }


@app.get("/api/v1/decisions")
def api_list_decisions(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    rows = list(
        session.execute(
            select(DecisionLog)
            .where(DecisionLog.character_id == profile.id)
            .order_by(DecisionLog.at.desc())
            .limit(30)
        ).scalars()
    )
    return {
        "decisions": [
            {
                "id": d.id,
                "decision": d.decision,
                "reason": d.reason,
                "signals": d.signals,
                "rejected_options": d.rejected_options,
                "expected_kpi": d.expected_kpi,
                "at": d.at.isoformat(),
            }
            for d in rows
        ]
    }


@app.get("/api/v1/logs")
def api_audit_logs(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    rows = list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.character_id == profile.id)
            .order_by(AuditLog.id.desc())
            .limit(50)
        ).scalars()
    )
    ok, seq_or_cnt = verify_audit_chain(session)
    return {
        "chain_valid": ok,
        "chain_seq": seq_or_cnt,
        "logs": [
            {
                "id": r.id,
                "actor": r.actor,
                "action": r.action,
                "resource_type": r.resource_type,
                "details": r.details_json,
                "entry_hash": r.entry_hash[:16],
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@app.post("/api/v1/chat")
async def api_direct_chat(
    body: ChatRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    mgr = LivePersonaSessionManager(_get_registry())
    return await mgr.direct_chat(
        session,
        character_id=profile.id,
        session_id=body.session_id,
        message=body.message,
        attachments=body.attachments,
        with_voice=body.with_voice,
    )


@app.post("/api/v1/live-call/token")
def api_live_call_token(session: Session = Depends(get_db)) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    mgr = LivePersonaSessionManager(_get_registry())
    return mgr.create_livekit_room_token(session, character_id=profile.id)


@app.post("/api/v1/live-call/turn")
async def api_live_call_turn(
    body: LiveCallTurnRequest, session: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
    mgr = LivePersonaSessionManager(_get_registry())
    return await mgr.handle_live_call_turn(
        session,
        character_id=profile.id,
        session_id=body.session_id,
        user_utterance=body.utterance,
        end_call_and_summarize=body.end_call_and_summarize,
    )


@app.websocket("/ws/live-call/{session_id}")
async def ws_live_call(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    mgr = LivePersonaSessionManager(_get_registry())
    try:
        while True:
            payload = await websocket.receive_json()
            text = str(payload.get("utterance", "")).strip()
            end_call = bool(payload.get("end_call", False))
            if not text:
                continue
            with get_session_factory()() as session:
                profile = session.execute(select(CharacterProfile).limit(1)).scalar_one()
                res = await mgr.handle_live_call_turn(
                    session,
                    character_id=profile.id,
                    session_id=session_id,
                    user_utterance=text,
                    end_call_and_summarize=end_call,
                )
            await websocket.send_json(res)
    except WebSocketDisconnect:
        return


@app.get("/api/v1/media")
def api_serve_media(path: str) -> FileResponse:
    settings = get_settings()
    base = Path(settings.local_storage_dir).resolve()
    target = Path(path).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(status_code=403, detail="Access denied outside storage directory")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(str(target))
