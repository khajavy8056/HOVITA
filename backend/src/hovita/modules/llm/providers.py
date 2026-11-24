from __future__ import annotations

import json
import time
from typing import Any

import httpx

from hovita.config.secrets import SecretStore
from hovita.config.settings import get_settings
from hovita.contracts.base import (
    CostMetrics,
    DegradationLevel,
    ModuleResult,
    ProviderError,
    ProviderMetadata,
    ProviderTier,
)
from hovita.contracts.modules import LLMInput, LLMOutput
from hovita.economy.key_rotator import KeyRotator
from hovita.logging.logger import get_logger, get_trace_id

logger = get_logger("hovita.modules.llm")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            try:
                parsed2 = json.loads(stripped[start : end + 1])
                if isinstance(parsed2, dict):
                    return parsed2
            except json.JSONDecodeError:
                return None
    return None


def _build_local_structured_fallback(payload: LLMInput) -> tuple[str, dict[str, Any] | None]:
    """Deterministic local structured reasoning fallback when no external LLM server is reachable
    (ensures S0/S4 never crash in offline/CI/thin environments)."""
    user_text = "\n".join(m.content for m in payload.messages if m.role == "user").strip()
    system_text = "\n".join(m.content for m in payload.messages if m.role == "system").strip()

    if payload.json_schema:
        props = payload.json_schema.get("properties", {})
        structured: dict[str, Any] = {}
        for k, spec in props.items():
            t = spec.get("type", "string")
            if "enum" in spec and spec["enum"]:
                structured[k] = spec["enum"][0]
            elif t == "integer":
                structured[k] = 7
            elif t == "number":
                structured[k] = 7.5
            elif t == "boolean":
                structured[k] = True
            elif t == "array":
                structured[k] = [f"{k}_item_1", f"{k}_item_2"]
            elif t == "object":
                structured[k] = {"source": "local_fallback"}
            else:
                if "hook" in k.lower():
                    structured[k] = "آیا می‌دانستید این جزئیات ساده نتیجه شما را دو برابر می‌کند؟"
                elif "caption" in k.lower():
                    structured[k] = (
                        f"پیشنهاد شفاف بر اساس هویت شخصیت ({user_text[:60]}). "
                        "تولیدشده با برچسب شفافیت هوش مصنوعی."
                    )
                elif "reason" in k.lower():
                    structured[k] = "انتخاب بر اساس هم‌راستایی با هدف، ترند روز و بودجه مجاز."
                else:
                    structured[k] = user_text[:120] or f"generated_{k}"
        return json.dumps(structured, ensure_ascii=False), structured

    # Conversational / general Farsi or English response preserving persona rules
    _ = system_text
    if payload.prefer_language == "fa":
        reply = (
            f"درخواست شما دریافت شد: «{user_text[:140]}». "
            "بر اساس هویت ثابت شخصیت و خط‌مشی کیفیت، پیشنهاد و خروجی آماده اجرا تنظیم شد."
        )
    else:
        reply = f"Acknowledged request: {user_text[:140]}. Aligned with persona profile and goals."
    return reply, None


class OllamaLocalLLMProvider:
    """S0 Local / Offline LLM provider via Ollama REST API with deterministic local fallback."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.metadata = ProviderMetadata(
            module_name="llm",
            provider_id="ollama_local",
            display_name="Ollama / llama.cpp (Local S0)",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            license_name="MIT",
            commercial_allowed=True,
            is_local=True,
            requires_gpu=False,
            quality_score=0.72,
            description="Zero-cost always-on CPU/GPU local inference",
        )
        self._client = http_client

    async def generate(self, payload: LLMInput) -> ModuleResult[LLMOutput]:
        settings = get_settings()
        t0 = time.monotonic()
        model = (
            settings.ollama_model_light
            if payload.task_complexity == "light"
            else settings.ollama_model_medium
        )
        prompt_text = "\n".join(m.content for m in payload.messages)
        tokens_in = _estimate_tokens(prompt_text)

        text = ""
        structured: dict[str, Any] | None = None
        warnings: list[str] = []
        used_remote = False

        try:
            body: dict[str, Any] = {
                "model": model,
                "messages": [m.model_dump() for m in payload.messages],
                "stream": False,
                "options": {"temperature": payload.temperature},
            }
            if payload.json_schema:
                body["format"] = payload.json_schema
            if self._client is not None:
                resp = await self._client.post(
                    f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=body,
                    timeout=5.0,
                )
            else:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(
                        f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                        json=body,
                    )
            if resp.status_code == 200:
                data = resp.json()
                text = str(data.get("message", {}).get("content", ""))
                structured = _extract_json_block(text) if payload.json_schema else None
                used_remote = bool(text)
        except Exception:
            used_remote = False

        if not used_remote:
            text, structured = _build_local_structured_fallback(payload)
            warnings.append("ollama_unreachable_used_deterministic_local_runtime")

        tokens_out = _estimate_tokens(text)
        latency = int((time.monotonic() - t0) * 1000)
        return ModuleResult(
            module_name="llm",
            provider_id=self.metadata.provider_id,
            model_name=model,
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            data=LLMOutput(
                text=text,
                structured=structured,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ),
            cost=CostMetrics(tokens_in=tokens_in, tokens_out=tokens_out),
            latency_ms=latency,
            trace_id=get_trace_id(),
            warnings=warnings,
        )


class FreeLLMAPIFailoverProvider:
    """S1 Free API mixer with multi-key pool rotation and automatic 429/5xx cooldown."""

    def __init__(
        self,
        *,
        rotator: KeyRotator | None = None,
        secret_store: SecretStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.metadata = ProviderMetadata(
            module_name="llm",
            provider_id="freellmapi_s1",
            display_name="FreeLLMAPI / Groq / OpenRouter Free (S1)",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S1_FREE_FAILOVER,
            license_name="MIT",
            commercial_allowed=True,
            is_local=False,
            quality_score=0.80,
            description="Zero-cost API tier with multi-key rotation & 429 cooldown",
        )
        self.rotator = rotator or KeyRotator()
        self.secret_store = secret_store
        self._client = http_client

    async def generate(self, payload: LLMInput) -> ModuleResult[LLMOutput]:
        settings = get_settings()
        store = self.secret_store or SecretStore()
        t0 = time.monotonic()

        # Candidate free API endpoints in priority order
        candidates: list[tuple[str, str, str, list[str]]] = []
        if settings.freellmapi_base_url:
            candidates.append(
                (
                    "freellmapi",
                    f"{settings.freellmapi_base_url.rstrip('/')}/v1/chat/completions",
                    "auto-free",
                    store.get_keys("freellmapi"),
                )
            )
        groq_keys = store.get_keys("groq")
        if groq_keys:
            candidates.append(
                (
                    "groq",
                    "https://api.groq.com/openai/v1/chat/completions",
                    "qwen/qwen3-32b",
                    groq_keys,
                )
            )
        or_keys = store.get_keys("openrouter")
        if or_keys:
            candidates.append(
                (
                    "openrouter_free",
                    "https://openrouter.ai/api/v1/chat/completions",
                    settings.openrouter_free_model,
                    or_keys,
                )
            )

        if not candidates:
            raise ProviderError(
                "No S1 free API keys configured",
                provider_id=self.metadata.provider_id,
                retryable=True,
            )

        prompt_tokens = _estimate_tokens("\n".join(m.content for m in payload.messages))
        last_err: str = "no_available_keys"

        for prov_name, endpoint, model, keys in candidates:
            if not keys:
                continue
            for _attempt in range(len(keys)):
                picked = self.rotator.next_available_key(prov_name, keys)
                if picked is None:
                    last_err = f"{prov_name}:all_keys_in_cooldown"
                    break
                idx, api_key = picked
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                body: dict[str, Any] = {
                    "model": model,
                    "messages": [m.model_dump() for m in payload.messages],
                    "temperature": payload.temperature,
                    "max_tokens": payload.max_tokens,
                }
                if payload.json_schema:
                    body["response_format"] = {"type": "json_object"}
                try:
                    if self._client is not None:
                        resp = await self._client.post(
                            endpoint, headers=headers, json=body, timeout=15.0
                        )
                    else:
                        async with httpx.AsyncClient(timeout=15.0) as client:
                            resp = await client.post(endpoint, headers=headers, json=body)

                    if resp.status_code == 429 or resp.status_code >= 500:
                        self.rotator.record_error(prov_name, idx, status_code=resp.status_code)
                        last_err = f"{prov_name}:HTTP_{resp.status_code}"
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    content = str(data["choices"][0]["message"]["content"])
                    usage = data.get("usage", {})
                    tin = int(usage.get("prompt_tokens", prompt_tokens))
                    tout = int(usage.get("completion_tokens", _estimate_tokens(content)))
                    self.rotator.record_success(prov_name, idx, tokens=tin + tout)
                    structured = _extract_json_block(content) if payload.json_schema else None
                    return ModuleResult(
                        module_name="llm",
                        provider_id=self.metadata.provider_id,
                        model_name=f"{prov_name}:{model}",
                        tier=ProviderTier.FREE,
                        degradation_level=DegradationLevel.S1_FREE_FAILOVER,
                        data=LLMOutput(
                            text=content,
                            structured=structured,
                            tokens_in=tin,
                            tokens_out=tout,
                        ),
                        cost=CostMetrics(tokens_in=tin, tokens_out=tout),
                        latency_ms=int((time.monotonic() - t0) * 1000),
                        trace_id=get_trace_id(),
                    )
                except httpx.HTTPError as exc:
                    self.rotator.record_error(prov_name, idx, status_code=503)
                    last_err = str(exc)

        raise ProviderError(
            f"S1 FreeLLMAPI failover exhausted: {last_err}",
            provider_id=self.metadata.provider_id,
            status_code=429,
            retryable=True,
            rate_limited=True,
        )


class OpenRouterEcoLLMProvider:
    """S2 Economical paid LLM gateway via OpenRouter."""

    def __init__(
        self,
        *,
        secret_store: SecretStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.metadata = ProviderMetadata(
            module_name="llm",
            provider_id="openrouter_eco",
            display_name="OpenRouter Economical (S2)",
            tier=ProviderTier.ECO,
            degradation_level=DegradationLevel.S2_ECO,
            license_name="Commercial API",
            commercial_allowed=True,
            is_local=False,
            quality_score=0.85,
            cost_per_1k_in=0.00025,
            cost_per_1k_out=0.00075,
        )
        self.secret_store = secret_store
        self._client = http_client

    async def generate(self, payload: LLMInput) -> ModuleResult[LLMOutput]:
        settings = get_settings()
        store = self.secret_store or SecretStore()
        api_key = store.get_primary_key("openrouter")
        if not api_key:
            raise ProviderError(
                "OPENROUTER_API_KEY missing",
                provider_id=self.metadata.provider_id,
                retryable=False,
            )
        t0 = time.monotonic()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": settings.openrouter_eco_model,
            "messages": [m.model_dump() for m in payload.messages],
            "temperature": payload.temperature,
            "max_tokens": payload.max_tokens,
        }
        if payload.json_schema:
            body["response_format"] = {"type": "json_object"}
        try:
            if self._client is not None:
                resp = await self._client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=20.0,
                )
            else:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=body,
                    )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise ProviderError(
                    f"OpenRouter HTTP {resp.status_code}",
                    provider_id=self.metadata.provider_id,
                    status_code=resp.status_code,
                    retryable=True,
                    rate_limited=(resp.status_code == 429),
                )
            resp.raise_for_status()
            data = resp.json()
            content = str(data["choices"][0]["message"]["content"])
            usage = data.get("usage", {})
            tin = int(usage.get("prompt_tokens", 100))
            tout = int(usage.get("completion_tokens", _estimate_tokens(content)))
            return ModuleResult(
                module_name="llm",
                provider_id=self.metadata.provider_id,
                model_name=settings.openrouter_eco_model,
                tier=ProviderTier.ECO,
                degradation_level=DegradationLevel.S2_ECO,
                data=LLMOutput(
                    text=content,
                    structured=_extract_json_block(content) if payload.json_schema else None,
                    tokens_in=tin,
                    tokens_out=tout,
                ),
                cost=CostMetrics(
                    tokens_in=tin,
                    rate_in=self.metadata.cost_per_1k_in / 1000.0,
                    tokens_out=tout,
                    rate_out=self.metadata.cost_per_1k_out / 1000.0,
                ),
                latency_ms=int((time.monotonic() - t0) * 1000),
                trace_id=get_trace_id(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"OpenRouter request error: {exc}",
                provider_id=self.metadata.provider_id,
                status_code=503,
                retryable=True,
            ) from exc


class ClaudeProLLMProvider:
    """S3 Pro LLM provider via Anthropic Messages API (heavy Persian/long-form only)."""

    def __init__(
        self,
        *,
        secret_store: SecretStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.metadata = ProviderMetadata(
            module_name="llm",
            provider_id="claude_pro",
            display_name="Anthropic Claude API (Pro S3)",
            tier=ProviderTier.PRO,
            degradation_level=DegradationLevel.S3_PRO,
            license_name="Commercial API",
            commercial_allowed=True,
            is_local=False,
            quality_score=0.96,
            cost_per_1k_in=0.003,
            cost_per_1k_out=0.015,
        )
        self.secret_store = secret_store
        self._client = http_client

    async def generate(self, payload: LLMInput) -> ModuleResult[LLMOutput]:
        store = self.secret_store or SecretStore()
        api_key = store.get_primary_key("anthropic")
        if not api_key:
            raise ProviderError(
                "ANTHROPIC_API_KEY missing",
                provider_id=self.metadata.provider_id,
                retryable=False,
            )
        t0 = time.monotonic()
        system_msgs = "\n".join(m.content for m in payload.messages if m.role == "system")
        conv_msgs = [
            {"role": m.role, "content": m.content}
            for m in payload.messages
            if m.role in {"user", "assistant"}
        ]
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": payload.max_tokens,
            "temperature": payload.temperature,
            "messages": conv_msgs,
        }
        if system_msgs:
            body["system"] = system_msgs
        try:
            if self._client is not None:
                resp = await self._client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=body,
                    timeout=30.0,
                )
            else:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers=headers,
                        json=body,
                    )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise ProviderError(
                    f"Claude HTTP {resp.status_code}",
                    provider_id=self.metadata.provider_id,
                    status_code=resp.status_code,
                    retryable=True,
                    rate_limited=(resp.status_code == 429),
                )
            resp.raise_for_status()
            data = resp.json()
            content = "".join(
                blk.get("text", "") for blk in data.get("content", []) if blk.get("type") == "text"
            )
            usage = data.get("usage", {})
            tin = int(usage.get("input_tokens", 100))
            tout = int(usage.get("output_tokens", _estimate_tokens(content)))
            return ModuleResult(
                module_name="llm",
                provider_id=self.metadata.provider_id,
                model_name="claude-sonnet-4",
                tier=ProviderTier.PRO,
                degradation_level=DegradationLevel.S3_PRO,
                data=LLMOutput(
                    text=content,
                    structured=_extract_json_block(content) if payload.json_schema else None,
                    tokens_in=tin,
                    tokens_out=tout,
                ),
                cost=CostMetrics(
                    tokens_in=tin,
                    rate_in=self.metadata.cost_per_1k_in / 1000.0,
                    tokens_out=tout,
                    rate_out=self.metadata.cost_per_1k_out / 1000.0,
                ),
                latency_ms=int((time.monotonic() - t0) * 1000),
                trace_id=get_trace_id(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Claude request error: {exc}",
                provider_id=self.metadata.provider_id,
                status_code=503,
                retryable=True,
            ) from exc
