from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from hovita.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CharacterProfile(Base):
    """Single-persona installation source of truth.

    Enforces `singleton_lock = 1` + UNIQUE so only ONE persona can exist per installation.
    """

    __tablename__ = "character_profile"
    __table_args__ = (
        CheckConstraint("singleton_lock = 1", name="ck_character_singleton_lock"),
        UniqueConstraint("singleton_lock", name="uq_character_singleton_lock"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    singleton_lock: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    job: Mapped[str] = mapped_column(String(64), nullable=False, default="influencer")
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kpis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    audience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    appearance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    locked_physical_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tone: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    do: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    dont: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    humor_level: Mapped[str] = mapped_column(String(32), nullable=False, default="subtle")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="fa")
    autonomy_level: Mapped[str] = mapped_column(String(8), nullable=False, default="L1")
    publish_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="approval_required"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class IdentityAsset(Base):
    __tablename__ = "identity_asset"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # ref_image|lora|prompt_lock|seed
    path: Mapped[str] = mapped_column(Text, nullable=False)
    angle: Mapped[str] = mapped_column(String(32), nullable=False, default="front")
    arcface_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class VoiceAsset(Base):
    __tablename__ = "voice_asset"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_sample: Mapped[str] = mapped_column(Text, nullable=False, default="")
    voice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="fa")
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=22050)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class BriefRecord(Base):
    __tablename__ = "brief"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    brief_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    kpis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    platforms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    budget: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    approval_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="approval_required"
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class SocialAccount(Base):
    __tablename__ = "social_account"
    __table_args__ = (UniqueConstraint("character_id", "platform", name="uq_social_platform"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    handle: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="oauth2")
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="connected")
    daily_publish_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    daily_reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ContentItem(Base):
    __tablename__ = "content_item"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="reel_9_16")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="new")  # trend|new|hybrid
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft"
    )  # draft->qa->awaiting_approval->scheduled->published
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    script_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assets: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    caption: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hashtags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="instagram")
    ai_disclosure_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    c2pa_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sensitive_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class QAResult(Base):
    __tablename__ = "qa_result"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False
    )
    gate: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # pass|revise|reject
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class DecisionLog(Base):
    __tablename__ = "decision_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    rejected_options: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    expected_kpi: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshot"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="instagram")
    date: Mapped[str] = mapped_column(String(16), nullable=False)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saves: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shares: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    follows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="official")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class MemoryEntry(Base):
    """Layer 2 Blackboard + Layer 4 Extracted Memory with strict namespace & invalidation."""

    __tablename__ = "memory_entry"
    __table_args__ = (Index("ix_memory_ns_status", "character_id", "status", "kind"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # fact|preference|episode
    semantic_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    owner_agent: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[list[float]] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="conversation")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.85)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    superseded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class JobRun(Base):
    __tablename__ = "job_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    content_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="init")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sec_video: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    images: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_call: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gpu_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class ApprovalRecord(Base):
    __tablename__ = "approval"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("content_item.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="L1")
    actor: Mapped[str] = mapped_column(String(32), nullable=False, default="human")  # human|auto
    decision: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class EnvironmentAsset(Base):
    __tablename__ = "environment_asset"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="real")  # real|virtual
    capture_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="multi_angle_photos"
    )
    media: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    keyframes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    panorama: Mapped[str | None] = mapped_column(Text, nullable=True)
    palette: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    fixed_props: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    product_zones: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    approved_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ProductItem(Base):
    """External Product Truth Registry — agent NEVER invents prices or specs."""

    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("character_id", "sku", name="uq_product_sku"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    buy_link: Mapped[str] = mapped_column(Text, nullable=False, default="")
    images: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class StageCheckpoint(Base):
    """11-stage production pipeline checkpoint record (resume from failure without re-run)."""

    __tablename__ = "stage_checkpoint"
    __table_args__ = (
        UniqueConstraint("thread_id", "stage_name", name="uq_thread_stage"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending|running|completed|failed|paused
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class AuditLog(Base):
    """Append-only, hash-chained immutable audit log."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(String(128), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class EmergencyState(Base):
    """Global emergency kill switch and resource policy state per installation."""

    __tablename__ = "emergency_state"
    __table_args__ = (
        CheckConstraint("singleton_lock = 1", name="ck_emergency_singleton"),
        UniqueConstraint("singleton_lock", name="uq_emergency_singleton"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="global")
    singleton_lock: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    character_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    browser_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    gpu_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="on_demand")


class GPUJobQueueItem(Base):
    __tablename__ = "gpu_job_queue"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2
    )  # 1=scheduled_today, 2=normal, 3=experiment
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    provider_used: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    gpu_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OnboardingProgress(Base):
    """F09 8-Step Setup Progress Tracker."""

    __tablename__ = "onboarding_progress"
    __table_args__ = (UniqueConstraint("character_id", "step_index", name="uq_onboard_step"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class ChatMessageRecord(Base):
    """Direct chat (F16) and Live voice/video call transcript & summary (F17)."""

    __tablename__ = "chat_message"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    character_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("character_profile.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|persona|system
    channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="text"
    )  # text|voice|video_call
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachments_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extracted_task_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
