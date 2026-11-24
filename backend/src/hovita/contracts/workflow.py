from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PRODUCTION_STAGES: tuple[str, ...] = (
    "01_idea_research",
    "02_script_variants",
    "03_image_gen",
    "04_video_motion",
    "05_voice_tts",
    "06_post_process_c2pa",
    "07_quality_gates",
    "08_human_approval",
    "09_schedule",
    "10_publish",
    "11_analytics",
)

PUBLISH_MODE_TO_AUTONOMY: dict[str, Literal["L0", "L1", "L2", "L3"]] = {
    "produce_only": "L0",
    "approval_required": "L1",
    "autonomous": "L2",
}

AUTONOMY_TO_PUBLISH_MODE: dict[str, Literal["produce_only", "approval_required", "autonomous"]] = {
    "L0": "produce_only",
    "L1": "approval_required",
    "L2": "autonomous",
    "L3": "autonomous",
}


class TaskGraphItem(BaseModel):
    topic: str
    format: Literal["reel_9_16", "carousel", "story", "short_video"] = "reel_9_16"
    production_mode: Literal["trend_motion", "new_idea", "hybrid"] = "new_idea"
    platform: Literal["instagram", "tiktok", "youtube", "sandbox"] = "instagram"
    scheduled_window: str = "18:00-21:00"
    expected_kpi: dict[str, Any] = Field(default_factory=dict)
    reason: str
    rejected_options: list[dict[str, str]] = Field(default_factory=list)
    signals_used: dict[str, Any] = Field(default_factory=dict)
    sensitive_content: bool = False
    product_sku: str | None = None


class QualityGateCheck(BaseModel):
    gate: Literal[
        "hook_3s",
        "face_consistency",
        "audio_video_sync",
        "style_goal_fit",
        "duration_rhythm",
        "safety_claims",
        "ai_disclosure",
    ]
    score: float
    threshold: float
    passed: bool
    model: str
    note: str
    remediation: str = ""


class QualityEvaluationSummary(BaseModel):
    content_id: str
    overall_verdict: Literal["pass", "revise", "reject"]
    revision_count: int = 0
    checks: list[QualityGateCheck] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionRecord(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    signals: dict[str, Any]
    decision: str
    reason: str
    rejected_options: list[dict[str, str]] = Field(default_factory=list)
    expected_kpi: dict[str, Any] = Field(default_factory=dict)
