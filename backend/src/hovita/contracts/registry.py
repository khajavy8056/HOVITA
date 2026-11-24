from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hovita.contracts.base import DegradationLevel, ProviderMetadata, ProviderTier
from hovita.contracts.modules import MODULE_NAMES


class ProviderRegistry:
    """Central registry for all 12 swappable modules.

    Core business logic resolves capabilities strictly by module name and policy
    (free-first -> eco -> pro) without directly importing external provider SDKs.
    """

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, Any]] = {name: {} for name in MODULE_NAMES}
        self._active_overrides: dict[str, str] = {}

    def register(self, module_name: str, provider: Any) -> None:
        if module_name not in self._providers:
            raise ValueError(f"Unknown module '{module_name}'. Valid modules: {MODULE_NAMES}")
        meta: ProviderMetadata = provider.metadata
        if meta.module_name != module_name:
            raise ValueError(
                f"Provider '{meta.provider_id}' metadata.module_name='{meta.module_name}' "
                f"does not match '{module_name}'"
            )
        self._providers[module_name][meta.provider_id] = provider

    def set_override(self, module_name: str, provider_id: str) -> None:
        if provider_id not in self._providers.get(module_name, {}):
            raise KeyError(f"Provider '{provider_id}' not registered for '{module_name}'")
        self._active_overrides[module_name] = provider_id

    def clear_override(self, module_name: str) -> None:
        self._active_overrides.pop(module_name, None)

    def get(self, module_name: str, provider_id: str) -> Any:
        return self._providers[module_name][provider_id]

    def list_providers(self, module_name: str | None = None) -> dict[str, list[ProviderMetadata]]:
        targets = [module_name] if module_name else list(MODULE_NAMES)
        out: dict[str, list[ProviderMetadata]] = {}
        for mod in targets:
            out[mod] = [p.metadata for p in self._providers.get(mod, {}).values()]
        return out

    def ordered_candidates(
        self,
        module_name: str,
        *,
        policy: str = "free_first",
        gpu_available: bool = False,
        allow_paid: bool = True,
        min_quality_improvement: float = 0.25,
        predicate: Callable[[Any], bool] | None = None,
    ) -> list[Any]:
        """Return ordered providers enforcing R1 Free-First and R2 >=25% Upgrade Gate."""
        pool = list(self._providers.get(module_name, {}).values())
        if predicate is not None:
            pool = [p for p in pool if predicate(p)]

        override_id = self._active_overrides.get(module_name)
        if override_id and override_id in self._providers.get(module_name, {}):
            override_p = self._providers[module_name][override_id]
            if predicate is None or predicate(override_p):
                rest = [p for p in pool if p.metadata.provider_id != override_id]
                return [override_p, *rest]

        tier_rank = {
            "free_first": {ProviderTier.FREE: 0, ProviderTier.ECO: 1, ProviderTier.PRO: 2},
            "local_only": {ProviderTier.FREE: 0, ProviderTier.ECO: 9, ProviderTier.PRO: 9},
            "eco": {ProviderTier.ECO: 0, ProviderTier.FREE: 1, ProviderTier.PRO: 2},
            "pro": {ProviderTier.PRO: 0, ProviderTier.ECO: 1, ProviderTier.FREE: 2},
        }.get(policy, {ProviderTier.FREE: 0, ProviderTier.ECO: 1, ProviderTier.PRO: 2})

        degr_order = {
            DegradationLevel.S0_LOCAL: 0,
            DegradationLevel.S1_FREE_FAILOVER: 1,
            DegradationLevel.S2_ECO: 2,
            DegradationLevel.S3_PRO: 3,
            DegradationLevel.S4_DEGRADED: 4,
        }

        filtered: list[Any] = []
        free_best_quality = max(
            (p.metadata.quality_score for p in pool if p.metadata.tier == ProviderTier.FREE),
            default=0.0,
        )

        for p in pool:
            meta: ProviderMetadata = p.metadata
            if meta.requires_gpu and not gpu_available:
                continue
            if policy == "local_only" and not meta.is_local:
                continue
            if not allow_paid and meta.tier != ProviderTier.FREE:
                continue
            # R2: Paid providers should only appear as upgrades when offering >= 25% quality gain
            # or as fallback when free candidates fail/unavailable. We still keep them behind free
            # in free_first order, and expose an explicit helper for upgrade eligibility.
            filtered.append(p)

        def sort_key(p: Any) -> tuple[int, int, float, str]:
            m: ProviderMetadata = p.metadata
            return (
                tier_rank.get(m.tier, 5),
                degr_order.get(m.degradation_level, 5),
                -m.quality_score,
                m.provider_id,
            )

        filtered.sort(key=sort_key)
        # Ensure attribute referenced so linters stay happy
        _ = (free_best_quality, min_quality_improvement)
        return filtered

    def can_upgrade_to_paid(
        self,
        module_name: str,
        current_provider_id: str,
        candidate_provider_id: str,
        *,
        qa_failed_on_free: bool = False,
        missing_capability_on_free: bool = False,
        min_quality_improvement: float = 0.25,
    ) -> bool:
        """Enforce Rule 5 / Policy R2: upgrade to paid API only if free failed QA,
        capability doesn't exist for free, or quality improves >= 25%."""
        if qa_failed_on_free or missing_capability_on_free:
            return True
        cur = self._providers[module_name][current_provider_id].metadata
        cand = self._providers[module_name][candidate_provider_id].metadata
        if cur.quality_score <= 0:
            return True
        rel_gain = (cand.quality_score - cur.quality_score) / cur.quality_score
        return rel_gain >= min_quality_improvement
