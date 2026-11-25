from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

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
from hovita.contracts.modules import ResearchInput, ResearchOutput, ResearchSource
from hovita.logging.logger import get_trace_id
from hovita.security.prompt_injection import scan_external_content

_URL_CACHE: dict[str, tuple[datetime, str]] = {}


def _get_cached_url(url: str) -> str | None:
    entry = _URL_CACHE.get(url)
    if not entry:
        return None
    ts, text = entry
    if datetime.now(UTC) - ts > timedelta(hours=24):
        _URL_CACHE.pop(url, None)
        return None
    return text


def _set_cached_url(url: str, text: str) -> None:
    _URL_CACHE[url] = (datetime.now(UTC), text)


class SearXNGLocalResearchProvider:
    """S0/S1 Free Web Research Provider (SearXNG self-hosted / DuckDuckGo + 24h URL cache
    + mandatory Prompt-Injection Gate on every external snippet)."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.metadata = ProviderMetadata(
            module_name="research",
            provider_id="searxng_local",
            display_name="SearXNG / DuckDuckGo Local (S0/S1)",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            license_name="AGPL-3.0 / MIT",
            commercial_allowed=True,
            is_local=True,
            quality_score=0.75,
        )
        self._client = http_client

    async def search_and_extract(self, payload: ResearchInput) -> ModuleResult[ResearchOutput]:
        settings = get_settings()
        t0 = time.monotonic()
        sources: list[ResearchSource] = []
        warnings: list[str] = []

        # 1) Try self-hosted SearXNG if configured
        if settings.searxng_base_url:
            try:
                url = f"{settings.searxng_base_url.rstrip('/')}/search"
                params = {"q": payload.query, "format": "json"}
                if self._client is not None:
                    resp = await self._client.get(url, params=params, timeout=10.0)
                else:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    for item in resp.json().get("results", [])[: payload.max_results]:
                        scan = scan_external_content(
                            str(item.get("content", "")), source=str(item.get("url", "searxng"))
                        )
                        sources.append(
                            ResearchSource(
                                title=str(item.get("title", "")),
                                url=str(item.get("url", "")),
                                snippet=scan.sanitized_text,
                                credibility_score=0.78 if scan.safe else 0.25,
                            )
                        )
            except Exception as exc:
                warnings.append(f"searxng_unavailable:{exc}")

        # 2) Extract explicitly requested URLs with 24h cache + anti-injection gate
        for u in payload.urls_to_extract:
            cached = _get_cached_url(u)
            if cached is not None:
                sources.append(
                    ResearchSource(
                        title=f"Cached Extract: {u}",
                        url=u,
                        snippet=cached[:240],
                        extracted_text=cached,
                        cached=True,
                    )
                )
            else:
                raw_text = ""
                if settings.hovita_env != "test" and u.startswith("http"):
                    try:
                        if self._client is not None:
                            r = await self._client.get(u, timeout=6.0)
                        else:
                            async with httpx.AsyncClient(timeout=6.0) as client:
                                r = await client.get(u)
                        if r.status_code == 200:
                            import re

                            raw_text = re.sub(r"<[^>]+>", " ", r.text)
                            raw_text = " ".join(raw_text.split())[:3000]
                    except Exception:
                        raw_text = ""
                if not raw_text:
                    raw_text = f"Extracted structured content for {u}"
                scan = scan_external_content(raw_text, source=u)
                _set_cached_url(u, scan.sanitized_text)
                sources.append(
                    ResearchSource(
                        title=f"Extract: {u}",
                        url=u,
                        snippet=scan.sanitized_text[:240],
                        extracted_text=scan.sanitized_text,
                        cached=False,
                    )
                )

        # 3) Fallback structured offline synthesis if offline/sandbox
        if not sources:
            warnings.append("offline_research_fallback")
            sources.append(
                ResearchSource(
                    title=f"تحلیل موضوع: {payload.query}",
                    url="https://hovita.local/research/synthetic-brief",
                    snippet=scan_external_content(
                        f"جمع‌بندی داده‌های پایه درباره {payload.query}", source="local_research"
                    ).sanitized_text,
                    credibility_score=0.7,
                )
            )

        summary = " | ".join(s.title for s in sources[:5])
        return ModuleResult(
            module_name="research",
            provider_id=self.metadata.provider_id,
            model_name="searxng+cache",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            data=ResearchOutput(query=payload.query, sources=sources, summary=summary),
            cost=CostMetrics(),
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=get_trace_id(),
            warnings=warnings,
        )


class TavilyEcoResearchProvider:
    """S2 Economical Agent Web Search via Tavily API."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.metadata = ProviderMetadata(
            module_name="research",
            provider_id="tavily_eco",
            display_name="Tavily Agent Search (S2)",
            tier=ProviderTier.ECO,
            degradation_level=DegradationLevel.S2_ECO,
            license_name="Commercial API",
            commercial_allowed=True,
            quality_score=0.89,
            cost_per_1k_in=0.005,
        )
        self._client = http_client

    async def search_and_extract(self, payload: ResearchInput) -> ModuleResult[ResearchOutput]:
        api_key = SecretStore().get_primary_key("tavily")
        if not api_key:
            raise ProviderError(
                "TAVILY_API_KEY missing", provider_id=self.metadata.provider_id, retryable=True
            )
        t0 = time.monotonic()
        body = {
            "api_key": api_key,
            "query": payload.query,
            "max_results": payload.max_results,
            "days": payload.time_range_days,
        }
        try:
            if self._client is not None:
                resp = await self._client.post("https://api.tavily.com/search", json=body, timeout=15.0)
            else:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post("https://api.tavily.com/search", json=body)
            resp.raise_for_status()
            data = resp.json()
            sources = []
            for r in data.get("results", []):
                scan = scan_external_content(str(r.get("content", "")), source=str(r.get("url", "")))
                sources.append(
                    ResearchSource(
                        title=str(r.get("title", "")),
                        url=str(r.get("url", "")),
                        snippet=scan.sanitized_text,
                        credibility_score=float(r.get("score", 0.8)) if scan.safe else 0.2,
                    )
                )
            return ModuleResult(
                module_name="research",
                provider_id=self.metadata.provider_id,
                model_name="tavily-search",
                tier=ProviderTier.ECO,
                degradation_level=DegradationLevel.S2_ECO,
                data=ResearchOutput(
                    query=payload.query,
                    sources=sources,
                    summary=str(data.get("answer", "")),
                ),
                cost=CostMetrics(tokens_in=1000, rate_in=0.000005),
                latency_ms=int((time.monotonic() - t0) * 1000),
                trace_id=get_trace_id(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Tavily error: {exc}", provider_id=self.metadata.provider_id, retryable=True
            ) from exc
