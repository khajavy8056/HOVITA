from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class ProviderTier(StrEnum):
    FREE = "free"
    ECO = "eco"
    PRO = "pro"


class DegradationLevel(StrEnum):
    S0_LOCAL = "S0"
    S1_FREE_FAILOVER = "S1"
    S2_ECO = "S2"
    S3_PRO = "S3"
    S4_DEGRADED = "S4"


class CostMetrics(BaseModel):
    """Standard cost breakdown using the exact policy formula:
    cost = tin*Rin + tout*Rout + sec_video*Rv + img*Ri + min_call*Rc + gpu_min*Rg
    """

    tokens_in: int = 0
    rate_in: float = 0.0
    tokens_out: int = 0
    rate_out: float = 0.0
    sec_video: float = 0.0
    rate_video: float = 0.0
    images: int = 0
    rate_image: float = 0.0
    min_call: float = 0.0
    rate_call: float = 0.0
    gpu_min: float = 0.0
    rate_gpu: float = 0.0
    energy_kwh: float = 0.0

    @property
    def total_usd(self) -> float:
        return round(
            self.tokens_in * self.rate_in
            + self.tokens_out * self.rate_out
            + self.sec_video * self.rate_video
            + self.images * self.rate_image
            + self.min_call * self.rate_call
            + self.gpu_min * self.rate_gpu,
            6,
        )


class ProviderMetadata(BaseModel):
    module_name: str
    provider_id: str
    display_name: str
    tier: ProviderTier
    degradation_level: DegradationLevel
    license_name: str
    commercial_allowed: bool = True
    is_local: bool = False
    requires_gpu: bool = False
    quality_score: float = Field(default=0.75, ge=0.0, le=1.0)
    cost_per_1k_in: float = 0.0
    cost_per_1k_out: float = 0.0
    cost_per_image: float = 0.0
    cost_per_video_sec: float = 0.0
    cost_per_call_min: float = 0.0
    description: str = ""


class ModuleResult(BaseModel, Generic[T]):
    ok: bool = True
    module_name: str
    provider_id: str
    model_name: str = ""
    tier: ProviderTier = ProviderTier.FREE
    degradation_level: DegradationLevel = DegradationLevel.S0_LOCAL
    data: T
    cost: CostMetrics = Field(default_factory=CostMetrics)
    latency_ms: int = 0
    trace_id: str = ""
    warnings: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProviderError(RuntimeError):
    """Structured error from a swappable module provider."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        status_code: int | None = None,
        retryable: bool = False,
        rate_limited: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.status_code = status_code
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.details = details or {}
