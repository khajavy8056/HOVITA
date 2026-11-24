from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

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
from hovita.contracts.modules import WorkflowRunInput, WorkflowRunOutput
from hovita.logging.logger import get_trace_id


class _CheckpointGraphState(TypedDict, total=False):
    thread_id: str
    current_stage: str
    payload: dict[str, Any]


def get_langgraph_checkpointer() -> SqliteSaver:
    """Return persistent LangGraph checkpointer backed by installation storage."""
    settings = get_settings()
    ckpt_path = Path(settings.local_storage_dir) / "langgraph_checkpoints.sqlite"
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ckpt_path), check_same_thread=False)
    return SqliteSaver(conn)


class LangGraphWorkflowProvider:
    """Primary S0 Workflow Engine powered by LangGraph (MIT) + persistent checkpoints."""

    def __init__(self) -> None:
        self.metadata = ProviderMetadata(
            module_name="workflow_engine",
            provider_id="langgraph_mit",
            display_name="LangGraph (MIT) + Persistent Checkpointer (S0)",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            license_name="MIT",
            commercial_allowed=True,
            is_local=True,
            quality_score=0.95,
        )

    async def run_workflow(self, payload: WorkflowRunInput) -> ModuleResult[WorkflowRunOutput]:
        t0 = time.monotonic()

        def step_execute(state: _CheckpointGraphState) -> _CheckpointGraphState:
            return {
                "thread_id": state.get("thread_id", payload.thread_id),
                "current_stage": payload.resume_from_stage or "completed",
                "payload": state.get("payload", payload.initial_state),
            }

        builder = StateGraph(_CheckpointGraphState)
        builder.add_node("execute", step_execute)
        builder.add_edge(START, "execute")
        builder.add_edge("execute", END)

        checkpointer = get_langgraph_checkpointer()
        graph = builder.compile(checkpointer=checkpointer)
        out_state = graph.invoke(
            {
                "thread_id": payload.thread_id,
                "current_stage": payload.resume_from_stage or "init",
                "payload": payload.initial_state,
            },
            config={"configurable": {"thread_id": payload.thread_id}},
        )

        return ModuleResult(
            module_name="workflow_engine",
            provider_id=self.metadata.provider_id,
            model_name="langgraph-stategraph",
            tier=ProviderTier.FREE,
            degradation_level=DegradationLevel.S0_LOCAL,
            data=WorkflowRunOutput(
                thread_id=payload.thread_id,
                status="resumed" if payload.resume_from_stage else "completed",
                current_stage=str(out_state.get("current_stage", "completed")),
                state=dict(out_state.get("payload", {})),
            ),
            cost=CostMetrics(),
            latency_ms=int((time.monotonic() - t0) * 1000),
            trace_id=get_trace_id(),
        )


class N8nBridgeWorkflowProvider:
    """Conditional n8n Webhook Bridge — enabled ONLY when customer self-hosts or holds commercial license."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.metadata = ProviderMetadata(
            module_name="workflow_engine",
            provider_id="n8n_conditional",
            display_name="n8n Bridge (Customer Self-Hosted / Licensed Only)",
            tier=ProviderTier.ECO,
            degradation_level=DegradationLevel.S2_ECO,
            license_name="Sustainable Use License",
            commercial_allowed=True,
            quality_score=0.90,
        )
        self._client = http_client

    async def run_workflow(self, payload: WorkflowRunInput) -> ModuleResult[WorkflowRunOutput]:
        settings = get_settings()
        if not settings.n8n_enabled or not settings.n8n_host:
            raise ProviderError(
                "n8n disabled (requires N8N_ENABLED=true with customer license/self-hosting).",
                provider_id=self.metadata.provider_id,
                retryable=False,
            )
        t0 = time.monotonic()
        api_key = SecretStore().get_primary_key("n8n") or ""
        url = f"{settings.n8n_host.rstrip('/')}/webhook/hovita-trigger"
        headers = {"X-N8N-API-KEY": api_key} if api_key else {}
        try:
            if self._client is not None:
                resp = await self._client.post(url, headers=headers, json=payload.model_dump(), timeout=15.0)
            else:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, headers=headers, json=payload.model_dump())
            resp.raise_for_status()
            return ModuleResult(
                module_name="workflow_engine",
                provider_id=self.metadata.provider_id,
                model_name="n8n-webhook",
                tier=ProviderTier.ECO,
                degradation_level=DegradationLevel.S2_ECO,
                data=WorkflowRunOutput(
                    thread_id=payload.thread_id,
                    status="completed",
                    current_stage="n8n_dispatched",
                    state=resp.json() if resp.content else {},
                ),
                cost=CostMetrics(),
                latency_ms=int((time.monotonic() - t0) * 1000),
                trace_id=get_trace_id(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"n8n dispatch failed: {exc}", provider_id=self.metadata.provider_id, retryable=True
            ) from exc
