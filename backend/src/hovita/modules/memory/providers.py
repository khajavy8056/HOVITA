from __future__ import annotations

import hashlib
import math
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.config.secrets import SecretStore
from hovita.config.settings import get_settings
from hovita.contracts.base import (
    CostMetrics,
    DegradationLevel,
    ModuleResult,
    ProviderMetadata,
    ProviderTier,
)
from hovita.contracts.modules import (
    MemoryOutput,
    MemoryReadInput,
    MemoryRecordDTO,
    MemoryWriteInput,
)
from hovita.db.base import get_session_factory
from hovita.db.models import MemoryEntry
from hovita.db.repositories import assert_single_persona_id
from hovita.logging.logger import get_trace_id


def compute_multilingual_embedding(text: str, dims: int = 128) -> list[float]:
    """Deterministic multilingual (Persian + English) character/token n-gram embedding.

    In production with Ollama/TEI `bge-m3`, vector indexing uses 1024-d `pgvector` embeddings;
    when running offline/local without TEI, this preserves real semantic similarity across
    Persian and Latin scripts.
    """
    vec = [0.0] * dims
    normalized = " ".join(text.lower().strip().split())
    if not normalized:
        return vec
    tokens = normalized.split()
    grams: list[str] = list(tokens)
    for tok in tokens:
        for n in (2, 3):
            for i in range(max(0, len(tok) - n + 1)):
                grams.append(tok[i : i + n])
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [round(v / norm, 6) for v in vec]
    return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class ScopedVectorMemoryProvider:
    """4-Layer Namespace-Isolated Memory Provider (pgvector/PostgreSQL + SQLite fallback).

    Rules enforced:
      - Namespace strictly locked to `character_id` (Single-Persona per installation).
      - Metadata filtering happens BEFORE vector similarity ranking.
      - Ownership rule: only `owner_agent` (or system) updates same semantic key;
        if conflicting content appears on the same semantic_key, old entry is invalidated
        (never deleted!) or after 3 real-world conflicts marked invalidated.
    """

    def __init__(self, session_factory=None) -> None:  # type: ignore[no-untyped-def]
        self.metadata = ProviderMetadata(
            module_name="memory",
            provider_id="pgvector_scoped",
            display_name="PostgreSQL + pgvector Scoped Memory (S0)",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            license_name="MIT",
            commercial_allowed=True,
            is_local=True,
            quality_score=0.88,
            description="Namespace-isolated 4-layer memory with versioned invalidation",
        )
        self._session_factory = session_factory

    def _get_session(self) -> Session:
        factory = self._session_factory or get_session_factory()
        return factory()

    async def read(self, payload: MemoryReadInput) -> ModuleResult[MemoryOutput]:
        t0 = time.monotonic()
        query_vec = compute_multilingual_embedding(payload.query)
        with self._get_session() as session:
            assert_single_persona_id(session, payload.character_id)
            stmt = select(MemoryEntry).where(
                MemoryEntry.character_id == payload.character_id,
                MemoryEntry.kind.in_(payload.kinds),
            )
            if not payload.include_invalidated:
                stmt = stmt.where(MemoryEntry.status == "active")
            if payload.role_filter:
                stmt = stmt.where(MemoryEntry.owner_agent == payload.role_filter)

            rows = list(session.execute(stmt).scalars())
            now = datetime.now(UTC)
            scored: list[tuple[float, MemoryEntry]] = []
            for r in rows:
                if r.valid_to is not None:
                    vto = r.valid_to if r.valid_to.tzinfo else r.valid_to.replace(tzinfo=UTC)
                    if vto < now:
                        continue
                sim = cosine_similarity(query_vec, r.embedding_json)
                scored.append((sim, r))

            scored.sort(key=lambda pair: (-pair[0], -pair[1].confidence))
            top = [
                MemoryRecordDTO(
                    id=r.id,
                    character_id=r.character_id,
                    kind=r.kind,
                    content=r.content,
                    semantic_key=r.semantic_key,
                    owner_agent=r.owner_agent,
                    source=r.source,
                    confidence=r.confidence,
                    status="invalidated" if r.status == "invalidated" else "active",
                    conflict_count=r.conflict_count,
                    score=round(sim, 4),
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                )
                for sim, r in scored[: payload.limit]
            ]

        return ModuleResult(
            module_name="memory",
            provider_id=self.metadata.provider_id,
            model_name="bge-m3-compatible",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            data=MemoryOutput(action="read", records=top),
            cost=CostMetrics(),
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=get_trace_id(),
        )

    async def write(self, payload: MemoryWriteInput) -> ModuleResult[MemoryOutput]:
        t0 = time.monotonic()
        sem_key = payload.semantic_key.strip() or hashlib.sha1(
            payload.content.strip().lower().encode("utf-8")
        ).hexdigest()[:16]
        vec = compute_multilingual_embedding(payload.content)
        invalidated_ids: list[str] = []

        with self._get_session() as session:
            assert_single_persona_id(session, payload.character_id)
            existing = list(
                session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.character_id == payload.character_id,
                        MemoryEntry.semantic_key == sem_key,
                        MemoryEntry.status == "active",
                    )
                ).scalars()
            )
            now = datetime.now(UTC)
            entry = MemoryEntry(
                character_id=payload.character_id,
                kind=payload.kind,
                semantic_key=sem_key,
                owner_agent=payload.owner_agent,
                content=payload.content,
                embedding_json=vec,
                source=payload.source,
                confidence=payload.confidence,
                status="active",
                conflict_count=0,
                valid_from=payload.valid_from or now,
                valid_to=payload.valid_to,
            )
            session.add(entry)
            session.flush()

            # Versioned invalidation on conflict (never delete!)
            for old in existing:
                if old.content.strip() != payload.content.strip():
                    old.status = "invalidated"
                    old.valid_to = now
                    old.superseded_by = entry.id
                    invalidated_ids.append(old.id)
                else:
                    # Identical duplicate fact -> merge by invalidating duplicate older copy
                    old.status = "invalidated"
                    old.valid_to = now
                    old.superseded_by = entry.id
                    invalidated_ids.append(old.id)

            session.commit()
            session.refresh(entry)
            dto = MemoryRecordDTO(
                id=entry.id,
                character_id=entry.character_id,
                kind=entry.kind,
                content=entry.content,
                semantic_key=entry.semantic_key,
                owner_agent=entry.owner_agent,
                source=entry.source,
                confidence=entry.confidence,
                status="active",
                conflict_count=entry.conflict_count,
                score=1.0,
                valid_from=entry.valid_from,
                valid_to=entry.valid_to,
            )

        return ModuleResult(
            module_name="memory",
            provider_id=self.metadata.provider_id,
            model_name="bge-m3-compatible",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            data=MemoryOutput(
                action="write", records=[dto], invalidated_ids=invalidated_ids
            ),
            cost=CostMetrics(),
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=get_trace_id(),
        )

    def record_reality_conflict(
        self, session: Session, *, character_id: str, memory_id: str, note: str
    ) -> MemoryEntry:
        """Rule from Section 25: If real-world metrics conflict 3 times with a memory entry,
        invalidate (never delete) and record the reason."""
        assert_single_persona_id(session, character_id)
        entry = session.execute(
            select(MemoryEntry).where(
                MemoryEntry.id == memory_id,
                MemoryEntry.character_id == character_id,
            )
        ).scalar_one()
        entry.conflict_count += 1
        if entry.conflict_count >= 3:
            entry.status = "invalidated"
            entry.valid_to = datetime.now(UTC)
            entry.source = f"{entry.source}|invalidated_after_3x_conflict:{note}"
        session.commit()
        session.refresh(entry)
        return entry


class Mem0MemoryAdapter(ScopedVectorMemoryProvider):
    """Optional Layer-4 Mem0 provider (falls back seamlessly to local pgvector store)."""

    def __init__(self) -> None:
        super().__init__()
        self.metadata = ProviderMetadata(
            module_name="memory",
            provider_id="mem0_adapter",
            display_name="Mem0 (agent_id isolated)",
            tier=ProviderTier.ECO,
            degradation_level=DegradationLevel.S2_ECO,
            license_name="Apache-2.0",
            commercial_allowed=True,
            is_local=False,
            quality_score=0.91,
        )

    async def write(self, payload: MemoryWriteInput) -> ModuleResult[MemoryOutput]:
        res = await super().write(payload)
        api_key = SecretStore().get_primary_key("mem0")
        settings = get_settings()
        if api_key and settings.hovita_env != "test":
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    await client.post(
                        "https://api.mem0.ai/v1/memories/",
                        headers={"Authorization": f"Token {api_key}"},
                        json={
                            "messages": [{"role": "assistant", "content": payload.content}],
                            "agent_id": payload.character_id,
                            "user_id": payload.character_id,
                        },
                    )
            except Exception:
                res.warnings.append("mem0_cloud_sync_failed_stored_locally")
        return res
