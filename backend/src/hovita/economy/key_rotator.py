from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class KeyQuotaState:
    provider: str
    key_index: int
    key_preview: str
    rpm_limit: int = 30
    rpd_limit: int = 1000
    tpm_limit: int = 250_000
    tpd_limit: int = 1_000_000
    requests_last_min: int = 0
    requests_today: int = 0
    tokens_last_min: int = 0
    tokens_today: int = 0
    cooldown_until: datetime | None = None
    consecutive_errors: int = 0
    minute_window_start: datetime = field(default_factory=lambda: datetime.now(UTC))
    day_window_start: datetime = field(default_factory=lambda: datetime.now(UTC))

    def _roll_windows(self, now: datetime) -> None:
        if now - self.minute_window_start >= timedelta(minutes=1):
            self.requests_last_min = 0
            self.tokens_last_min = 0
            self.minute_window_start = now
        if now - self.day_window_start >= timedelta(days=1):
            self.requests_today = 0
            self.tokens_today = 0
            self.day_window_start = now

    def is_available(self) -> bool:
        now = datetime.now(UTC)
        self._roll_windows(now)
        if self.cooldown_until and now < self.cooldown_until:
            return False
        if self.requests_last_min >= self.rpm_limit or self.requests_today >= self.rpd_limit:
            return False
        if self.tokens_last_min >= self.tpm_limit or self.tokens_today >= self.tpd_limit:
            return False
        return True

    def is_near_limit_80pct(self) -> bool:
        return (
            self.requests_today >= int(self.rpd_limit * 0.8)
            or self.tokens_today >= int(self.tpd_limit * 0.8)
        )


class KeyRotator:
    """Per-key RPM/RPD/TPM/TPD quota tracker + automatic 429/5xx cooldown & failover."""

    def __init__(self) -> None:
        self._pools: dict[str, list[KeyQuotaState]] = {}

    def register_keys(
        self,
        provider: str,
        keys: list[str],
        *,
        rpm_limit: int = 30,
        rpd_limit: int = 1000,
        tpm_limit: int = 250_000,
        tpd_limit: int = 1_000_000,
    ) -> None:
        states: list[KeyQuotaState] = []
        for idx, k in enumerate(keys):
            preview = f"{k[:4]}...{k[-2:]}" if len(k) > 6 else "***"
            states.append(
                KeyQuotaState(
                    provider=provider,
                    key_index=idx,
                    key_preview=preview,
                    rpm_limit=rpm_limit,
                    rpd_limit=rpd_limit,
                    tpm_limit=tpm_limit,
                    tpd_limit=tpd_limit,
                )
            )
        self._pools[provider] = states

    def next_available_key(self, provider: str, keys: list[str]) -> tuple[int, str] | None:
        if provider not in self._pools or len(self._pools[provider]) != len(keys):
            self.register_keys(provider, keys)
        pool = self._pools[provider]
        for idx, state in enumerate(pool):
            if state.is_available():
                return idx, keys[idx]
        return None

    def record_success(self, provider: str, key_index: int, tokens: int = 0) -> None:
        state = self._pools[provider][key_index]
        now = datetime.now(UTC)
        state._roll_windows(now)
        state.requests_last_min += 1
        state.requests_today += 1
        state.tokens_last_min += tokens
        state.tokens_today += tokens
        state.consecutive_errors = 0

    def record_error(
        self, provider: str, key_index: int, *, status_code: int, base_cooldown_sec: int = 30
    ) -> int:
        state = self._pools[provider][key_index]
        state.consecutive_errors += 1
        backoff = min(base_cooldown_sec * (2 ** (state.consecutive_errors - 1)), 600)
        if status_code == 429 or status_code >= 500:
            state.cooldown_until = datetime.now(UTC) + timedelta(seconds=backoff)
        return backoff

    async def backoff_sleep(self, attempt: int) -> None:
        delay = min(0.5 * (2**attempt), 8.0)
        await asyncio.sleep(delay)

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        out: dict[str, list[dict[str, object]]] = {}
        for provider, states in self._pools.items():
            out[provider] = [
                {
                    "key_index": s.key_index,
                    "key_preview": s.key_preview,
                    "available": s.is_available(),
                    "near_limit_80pct": s.is_near_limit_80pct(),
                    "requests_today": s.requests_today,
                    "rpd_limit": s.rpd_limit,
                    "tokens_today": s.tokens_today,
                    "tpd_limit": s.tpd_limit,
                    "cooldown_until": s.cooldown_until.isoformat() if s.cooldown_until else None,
                }
                for s in states
            ]
        return out
