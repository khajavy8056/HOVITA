from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from hovita.contracts.base import ModuleResult, ProviderMetadata

MODULE_NAMES: tuple[str, ...] = (
    "llm",
    "memory",
    "research",
    "image_gen",
    "video_gen",
    "motion_transfer",
    "tts",
    "stt",
    "video_edit",
    "social_analytics",
    "publisher",
    "workflow_engine",
)


# ---------------------------------------------------------------------------
# 1. LLM Gateway Module
# ---------------------------------------------------------------------------
class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMInput(BaseModel):
    messages: list[LLMMessage]
    json_schema: dict[str, Any] | None = None
    task_complexity: Literal["light", "medium", "heavy"] = "light"
    temperature: float = 0.3
    max_tokens: int = 1200
    prefer_language: Literal["fa", "en"] = "fa"


class LLMOutput(BaseModel):
    text: str
    structured: dict[str, Any] | None = None
    finish_reason: str = "stop"
    tokens_in: int = 0
    tokens_out: int = 0


@runtime_checkable
class LLMProvider(Protocol):
    metadata: ProviderMetadata

    async def generate(self, payload: LLMInput) -> ModuleResult[LLMOutput]: ...


# ---------------------------------------------------------------------------
# 2. Memory Module (4-Layer Scoped Namespace)
# ---------------------------------------------------------------------------
class MemoryReadInput(BaseModel):
    character_id: str
    query: str
    kinds: list[Literal["fact", "preference", "episode", "decision", "environment", "product"]] = (
        Field(default=["fact", "preference", "episode"])
    )
    role_filter: str | None = None
    limit: int = 10
    include_invalidated: bool = False


class MemoryWriteInput(BaseModel):
    character_id: str
    kind: Literal["fact", "preference", "episode"]
    content: str
    semantic_key: str = ""
    owner_agent: str = "system"
    source: str = "conversation"
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class MemoryRecordDTO(BaseModel):
    id: str
    character_id: str
    kind: str
    content: str
    semantic_key: str
    owner_agent: str
    source: str
    confidence: float
    status: Literal["active", "invalidated"]
    conflict_count: int = 0
    score: float = 1.0
    valid_from: datetime
    valid_to: datetime | None = None


class MemoryOutput(BaseModel):
    action: Literal["read", "write", "invalidate", "extract"]
    records: list[MemoryRecordDTO] = Field(default_factory=list)
    invalidated_ids: list[str] = Field(default_factory=list)


@runtime_checkable
class MemoryProvider(Protocol):
    metadata: ProviderMetadata

    async def read(self, payload: MemoryReadInput) -> ModuleResult[MemoryOutput]: ...

    async def write(self, payload: MemoryWriteInput) -> ModuleResult[MemoryOutput]: ...


# ---------------------------------------------------------------------------
# 3. Research & Search Module
# ---------------------------------------------------------------------------
class ResearchInput(BaseModel):
    query: str
    time_range_days: int = 7
    max_results: int = 5
    urls_to_extract: list[str] = Field(default_factory=list)


class ResearchSource(BaseModel):
    title: str
    url: str
    snippet: str
    extracted_text: str = ""
    credibility_score: float = Field(default=0.7, ge=0.0, le=1.0)
    independent_source: bool = True
    cached: bool = False


class ResearchOutput(BaseModel):
    query: str
    sources: list[ResearchSource] = Field(default_factory=list)
    summary: str = ""


@runtime_checkable
class ResearchProvider(Protocol):
    metadata: ProviderMetadata

    async def search_and_extract(self, payload: ResearchInput) -> ModuleResult[ResearchOutput]: ...


# ---------------------------------------------------------------------------
# 4. Image Generation Module
# ---------------------------------------------------------------------------
class ImageGenInput(BaseModel):
    character_id: str
    prompt: str
    locked_physical_prompt: str = ""
    angle: Literal[
        "front", "left_profile", "right_profile", "three_quarter", "full_body", "closeup", "scene"
    ] = "front"
    width: int = 1024
    height: int = 1365
    seed: int = 42
    reference_image_paths: list[str] = Field(default_factory=list)
    lora_path: str | None = None
    adapter_weight: float = Field(default=0.82, ge=0.0, le=1.0)
    environment_ref_path: str | None = None


class ImageGenOutput(BaseModel):
    file_path: str
    seed: int
    model: str
    width: int
    height: int
    arcface_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ImageGenProvider(Protocol):
    metadata: ProviderMetadata

    async def generate_image(self, payload: ImageGenInput) -> ModuleResult[ImageGenOutput]: ...


# ---------------------------------------------------------------------------
# 5. Video Generation Module
# ---------------------------------------------------------------------------
class VideoGenInput(BaseModel):
    character_id: str
    prompt: str
    keyframe_image_path: str | None = None
    duration_sec: int = Field(default=18, ge=3, le=60)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    fps: int = 24
    seed: int = 42


class VideoGenOutput(BaseModel):
    file_path: str
    duration_sec: float
    width: int
    height: int
    fps: int
    quality_label: str = "720p"


@runtime_checkable
class VideoGenProvider(Protocol):
    metadata: ProviderMetadata

    async def generate_video(self, payload: VideoGenInput) -> ModuleResult[VideoGenOutput]: ...


# ---------------------------------------------------------------------------
# 6. Motion Transfer Module
# ---------------------------------------------------------------------------
class MotionTransferInput(BaseModel):
    character_id: str
    character_image_path: str
    reference_video_path: str
    mode: Literal["animation", "replacement"] = "animation"
    preserve_lighting: bool = True
    resolution: Literal["720p", "1080p"] = "720p"


class MotionTransferOutput(BaseModel):
    file_path: str
    duration_sec: float
    pose_landmarks_count: int = 0
    face_similarity: float = 0.0
    license_ok: bool = True


@runtime_checkable
class MotionTransferProvider(Protocol):
    metadata: ProviderMetadata

    async def transfer_motion(
        self, payload: MotionTransferInput
    ) -> ModuleResult[MotionTransferOutput]: ...


# ---------------------------------------------------------------------------
# 7. TTS (Voice Synthesis & Cloning) Module
# ---------------------------------------------------------------------------
class TTSInput(BaseModel):
    character_id: str
    text: str
    voice_id: str
    language: Literal["fa", "en"] = "fa"
    ref_sample_path: str | None = None
    energy: Literal["calm", "balanced", "energetic"] = "balanced"
    speed: float = 1.0


class TTSOutput(BaseModel):
    file_path: str
    voice_id: str
    language: str
    duration_sec: float
    sample_rate: int = 22050
    pronunciation_error_rate: float = 0.02


@runtime_checkable
class TTSProvider(Protocol):
    metadata: ProviderMetadata

    async def synthesize(self, payload: TTSInput) -> ModuleResult[TTSOutput]: ...


# ---------------------------------------------------------------------------
# 8. STT (Speech-to-Text) Module
# ---------------------------------------------------------------------------
class WordTimestamp(BaseModel):
    word: str
    start_sec: float
    end_sec: float


class STTInput(BaseModel):
    audio_or_video_path: str
    language: Literal["fa", "en", "auto"] = "auto"


class STTOutput(BaseModel):
    transcript: str
    language: str
    words: list[WordTimestamp] = Field(default_factory=list)
    duration_sec: float = 0.0


@runtime_checkable
class STTProvider(Protocol):
    metadata: ProviderMetadata

    async def transcribe(self, payload: STTInput) -> ModuleResult[STTOutput]: ...


# ---------------------------------------------------------------------------
# 9. Video Editing & Post-Processing Module
# ---------------------------------------------------------------------------
class VideoEditInput(BaseModel):
    character_id: str
    video_paths: list[str]
    audio_path: str | None = None
    subtitles_srt: str | None = None
    target_aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    target_duration_sec: int = Field(default=20, ge=5, le=60)
    min_cuts_per_5s: float = 1.0
    inject_c2pa: bool = True
    ai_disclosure_label: str = "AI-generated synthetic persona content (HOVITA)"


class VideoEditOutput(BaseModel):
    output_mp4_path: str
    cover_image_path: str
    duration_sec: float
    scene_cuts_count: int
    lip_audio_overlap_pct: float
    c2pa_verified: bool
    file_sha256: str


@runtime_checkable
class VideoEditProvider(Protocol):
    metadata: ProviderMetadata

    async def compose(self, payload: VideoEditInput) -> ModuleResult[VideoEditOutput]: ...


# ---------------------------------------------------------------------------
# 10. Social Analytics & Trends Module
# ---------------------------------------------------------------------------
class SocialAnalyticsInput(BaseModel):
    character_id: str
    platform: Literal["instagram", "tiktok", "youtube"]
    account_handle: str
    action: Literal["page_insights", "post_insights", "trends", "competitors"]
    post_ids: list[str] = Field(default_factory=list)
    niche_keywords: list[str] = Field(default_factory=list)


class MetricDataPoint(BaseModel):
    target_id: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    saves: int = 0
    shares: int = 0
    followers_delta: int = 0
    sales_count: int = 0
    revenue_usd: float = 0.0
    confidence: Literal["official", "estimated"] = "official"


class TrendSignal(BaseModel):
    topic: str
    source_count: int
    growth_score: float
    age_hours: int
    persona_fit: float
    license_risk: Literal["low", "medium", "high"] = "low"
    sample_urls: list[str] = Field(default_factory=list)


class SocialAnalyticsOutput(BaseModel):
    platform: str
    confidence: Literal["official", "estimated"]
    metrics: list[MetricDataPoint] = Field(default_factory=list)
    trends: list[TrendSignal] = Field(default_factory=list)
    page_snapshot: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class SocialAnalyticsProvider(Protocol):
    metadata: ProviderMetadata

    async def fetch(self, payload: SocialAnalyticsInput) -> ModuleResult[SocialAnalyticsOutput]: ...


# ---------------------------------------------------------------------------
# 11. Official Publisher Module
# ---------------------------------------------------------------------------
class PublishInput(BaseModel):
    character_id: str
    content_id: str
    platform: Literal["instagram", "tiktok", "youtube", "sandbox"]
    media_path: str
    cover_path: str | None = None
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    ai_disclosure: bool = True
    paid_partnership: bool = False
    scheduled_for: datetime | None = None
    idempotency_key: str


class PublishOutput(BaseModel):
    published_id: str
    platform: str
    status_code: int = 200
    permalink: str = ""
    ai_label_applied: bool = True
    c2pa_preserved: bool = True
    idempotency_key: str


@runtime_checkable
class PublisherProvider(Protocol):
    metadata: ProviderMetadata

    async def publish(self, payload: PublishInput) -> ModuleResult[PublishOutput]: ...


# ---------------------------------------------------------------------------
# 12. Workflow Engine Module
# ---------------------------------------------------------------------------
class WorkflowRunInput(BaseModel):
    character_id: str
    thread_id: str
    pipeline_name: str
    initial_state: dict[str, Any] = Field(default_factory=dict)
    resume_from_stage: str | None = None


class WorkflowRunOutput(BaseModel):
    thread_id: str
    status: Literal["completed", "paused_for_approval", "failed", "resumed"]
    current_stage: str
    state: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class WorkflowEngineProvider(Protocol):
    metadata: ProviderMetadata

    async def run_workflow(self, payload: WorkflowRunInput) -> ModuleResult[WorkflowRunOutput]: ...


def export_all_module_schemas() -> dict[str, dict[str, Any]]:
    """Export JSON Schemas for all 12 standard module contracts."""
    pairs: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
        "llm": (LLMInput, LLMOutput),
        "memory": (MemoryReadInput, MemoryOutput),
        "research": (ResearchInput, ResearchOutput),
        "image_gen": (ImageGenInput, ImageGenOutput),
        "video_gen": (VideoGenInput, VideoGenOutput),
        "motion_transfer": (MotionTransferInput, MotionTransferOutput),
        "tts": (TTSInput, TTSOutput),
        "stt": (STTInput, STTOutput),
        "video_edit": (VideoEditInput, VideoEditOutput),
        "social_analytics": (SocialAnalyticsInput, SocialAnalyticsOutput),
        "publisher": (PublishInput, PublishOutput),
        "workflow_engine": (WorkflowRunInput, WorkflowRunOutput),
    }
    return {
        name: {
            "input_schema": inp.model_json_schema(),
            "output_schema": out.model_json_schema(),
        }
        for name, (inp, out) in pairs.items()
    }
