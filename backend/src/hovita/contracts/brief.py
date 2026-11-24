from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PersonaKPIs(BaseModel):
    views_30d: int = 100_000
    followers_delta: int = 5_000
    engagement_rate_min: float = 0.05
    sales_target_30d: int = 0
    sponsor_ready_milestone: str = ""


class PageConfig(BaseModel):
    state: Literal["fresh", "existing"] = "fresh"
    handle: str = "@hovita_persona"
    connect: list[str] = Field(
        default_factory=lambda: ["instagram_graph_api", "tiktok_content_posting"]
    )
    audit_on_start: bool = True


class PersonaBrief(BaseModel):
    """Exact persona-brief.json v1.0 contract from Section 09."""

    brief_version: str = "1.0"
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).date().isoformat())
    goal: str
    kpis: PersonaKPIs = Field(default_factory=PersonaKPIs)
    platforms: list[Literal["instagram_reels", "tiktok", "youtube_shorts"]] = Field(
        default=["instagram_reels", "tiktok"]
    )
    monthly_budget_usd: float = Field(default=60.0, ge=0.0)
    page: PageConfig = Field(default_factory=PageConfig)
    constraints: dict[str, Any] = Field(
        default_factory=lambda: {
            "allow_watermark": False,
            "commercial_license_required": True,
            "prefer_free_tools": True,
        }
    )
    approval_policy: Literal["produce_only", "approval_required", "autonomous"] = (
        "approval_required"
    )


class CharacterProfileContract(BaseModel):
    """Source-of-truth Layer 1 Profile (modified ONLY on explicit user approval)."""

    id: str
    name: str
    status: Literal["draft", "identity_locked", "active", "paused"] = "draft"
    job: Literal["influencer", "seller", "specialist", "support", "researcher"] = "influencer"
    goal: str = ""
    kpis: dict[str, Any] = Field(default_factory=dict)
    audience: str = ""
    appearance: dict[str, str] = Field(default_factory=dict)
    locked_physical_prompt: str = ""
    tone: list[str] = Field(default_factory=list)
    do: list[str] = Field(default_factory=list)
    dont: list[str] = Field(default_factory=list)
    humor_level: Literal["none", "subtle", "witty"] = "subtle"
    language: Literal["fa", "en", "fa-en"] = "fa"
    autonomy_level: Literal["L0", "L1", "L2", "L3"] = "L1"
    publish_mode: Literal["produce_only", "approval_required", "autonomous"] = "approval_required"
    version: int = 1


class ConversationalBuilderQuestion(BaseModel):
    step: int
    field_key: str
    question_fa: str
    question_en: str
    missing_fields: list[str] = Field(default_factory=list)


class ConversationalBuilderResponse(BaseModel):
    completed: bool
    question_count: int
    missing_fields: list[str]
    next_question: ConversationalBuilderQuestion | None = None
    profile: CharacterProfileContract | None = None
    brief: PersonaBrief | None = None


class EnvironmentPackContract(BaseModel):
    id: str
    kind: Literal["real", "virtual"] = "real"
    capture_method: Literal["walkthrough_video", "multi_angle_photos", "chat", "virtual_ai"] = (
        "multi_angle_photos"
    )
    media: list[str] = Field(default_factory=list)
    keyframes: list[str] = Field(default_factory=list)
    panorama: str | None = None
    palette: list[str] = Field(default_factory=list)
    fixed_props: list[str] = Field(default_factory=list)
    product_zones: list[str] = Field(default_factory=list)
    approved_version: int = 0


class ProductTruthContract(BaseModel):
    id: str
    name: str
    brand: str = ""
    sku: str
    price: float = Field(ge=0.0)
    discount: float = Field(default=0.0, ge=0.0)
    stock: int = Field(default=0, ge=0)
    features: dict[str, Any] = Field(default_factory=dict)
    buy_link: str = ""
    images: list[str] = Field(default_factory=list)
    status: Literal["active", "inactive", "out_of_stock"] = "active"
    version: int = 1
    source: Literal["manual", "xlsx", "csv", "api"] = "manual"
