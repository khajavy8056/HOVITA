from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server-side configuration & policies for a single-persona HOVITA installation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Single-Persona Installation Identity
    hovita_env: Literal["development", "staging", "production", "test"] = "development"
    character_id: str = Field(default="persona-001", min_length=1, max_length=128)
    character_name: str = Field(default="Hovita Persona", min_length=1, max_length=255)
    app_port: int = 8000
    web_port: int = 3000
    log_level: str = "INFO"

    # Storage & Database
    database_url: str = "sqlite:///./data/hovita.db"
    redis_url: str = "memory://"
    storage_backend: Literal["local", "minio"] = "local"
    local_storage_dir: Path = Path("./data/storage")
    minio_endpoint: str = "minio:9000"
    minio_access_key: SecretStr = SecretStr("minioadmin")
    minio_secret_key: SecretStr = SecretStr("minioadmin")
    minio_bucket: str = "hovita-assets"
    minio_secure: bool = False

    # Conditional n8n bridge (customer self-hosted or commercial license only)
    n8n_enabled: bool = False
    n8n_host: str = ""
    n8n_api_key: SecretStr = SecretStr("")

    # Model Gateway & Providers (Free-First)
    brain_model_gate: Literal["free_first", "eco", "pro", "local_only"] = "free_first"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model_light: str = "qwen3:8b"
    ollama_model_medium: str = "qwen3:14b"
    freellmapi_base_url: str = ""
    freellmapi_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_free_model: str = "qwen/qwen3-14b:free"
    openrouter_eco_model: str = "deepseek/deepseek-chat"
    groq_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    fal_key: SecretStr = SecretStr("")
    hf_token: SecretStr = SecretStr("")
    comfyui_base_url: str = "http://localhost:8188"
    pocket_tts_farsi_url: str = "http://localhost:8010"
    faster_whisper_model: str = "small"

    # Official Social APIs
    meta_app_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    instagram_access_token: SecretStr = SecretStr("")
    instagram_user_id: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: SecretStr = SecretStr("")
    tiktok_access_token: SecretStr = SecretStr("")
    youtube_api_key: SecretStr = SecretStr("")
    youtube_oauth_token: SecretStr = SecretStr("")

    # Trends & Research
    searxng_base_url: str = ""
    tavily_api_key: SecretStr = SecretStr("")
    firecrawl_api_key: SecretStr = SecretStr("")
    ensembledata_api_key: SecretStr = SecretStr("")
    apify_token: SecretStr = SecretStr("")

    # Memory & Observability
    mem0_api_key: SecretStr = SecretStr("")
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "http://localhost:3001"

    # Live Call
    livekit_url: str = ""
    livekit_api_key: SecretStr = SecretStr("")
    livekit_api_secret: SecretStr = SecretStr("")
    heygen_api_key: SecretStr = SecretStr("")
    tavus_api_key: SecretStr = SecretStr("")
    d_id_api_key: SecretStr = SecretStr("")

    # Policies & Quality Gates
    qa_face_threshold: float = 0.72
    qa_hook_min_score: float = 6.0
    qa_lip_sync_min_pct: float = 95.0
    qa_min_duration_sec: int = 15
    qa_max_duration_sec: int = 45
    qa_max_revisions: int = 2
    daily_budget_usd: float = 3.0
    budget_warn_pct: float = 0.80
    autonomy_level: Literal["L0", "L1", "L2", "L3"] = "L1"
    publish_mode: Literal["produce_only", "approval_required", "autonomous"] = "approval_required"

    # Platform Quotas & GPU Resource Management
    ig_daily_publish_limit: int = 50
    ig_hourly_api_limit: int = 200
    tiktok_daily_publish_limit: int = 25
    gpu_mode: Literal["on_demand", "always_on", "hourly_rental"] = "on_demand"
    gpu_available: bool = False
    gpu_idle_sleep_minutes: int = 10
    gpu_power_watts: float = 250.0
    gpu_electricity_usd_per_kwh: float = 0.12
    gpu_rental_usd_per_hour: float = 0.45

    # Internal Browser Layer
    browser_layer_enabled: bool = True
    browser_daily_read_limit: int = 100
    browser_allow_fallback_publish: bool = False

    # Secret Store & Admin Security
    secret_master_key: SecretStr = SecretStr("")
    secret_store_path: Path = Path("./data/secrets.enc.json")
    admin_api_key: SecretStr = SecretStr("")
    scheduler_interval_sec: int = 300

    def collected_secret_values(self) -> list[str]:
        """Return non-empty secret strings for log redaction."""
        secrets: list[str] = []
        for field_name in self.__class__.model_fields:
            val = getattr(self, field_name)
            if isinstance(val, SecretStr):
                raw = val.get_secret_value().strip()
                if len(raw) >= 4:
                    secrets.append(raw)
        return secrets


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
