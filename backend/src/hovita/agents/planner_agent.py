from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.contracts.modules import (
    LLMInput,
    LLMMessage,
    MemoryReadInput,
    SocialAnalyticsInput,
)
from hovita.contracts.registry import ProviderRegistry
from hovita.contracts.workflow import DecisionRecord, TaskGraphItem
from hovita.db.models import DecisionLog, MetricSnapshot, ProductItem
from hovita.db.repositories import assert_single_persona_id
from hovita.modules.llm.router import run_llm_with_failover
from hovita.modules.workflow_engine.providers import get_langgraph_checkpointer


class PlannerGraphState(TypedDict, total=False):
    character_id: str
    goal: str
    job: str
    tone: list[str]
    memories: list[str]
    trends: list[dict[str, Any]]
    products: list[dict[str, Any]]
    latest_metrics: dict[str, Any]
    task_graph: list[dict[str, Any]]
    decision_record: dict[str, Any]


class PlannerAgent:
    """Agent #1 (LangGraph): Converts persona goal + scoped memory + official insights + trends
    into a reasoned TaskGraph with explicit DecisionLog (`signals`, `reason`, `rejected_options`)."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry
        self._checkpointer = get_langgraph_checkpointer()

    async def plan_next_content(
        self,
        session: Session,
        *,
        character_id: str,
        thread_id: str,
        preferred_platform: str = "instagram",
        user_directive: str = "",
    ) -> dict[str, Any]:
        profile = assert_single_persona_id(session, character_id)

        # Read Layer 2 Scoped Memory (character_id namespace ONLY)
        mem_provider = self.registry.ordered_candidates("memory")[0]
        mem_res = await mem_provider.read(
            MemoryReadInput(
                character_id=character_id,
                query=f"{profile.goal} {user_directive}".strip(),
                limit=5,
            )
        )
        memories = [m.content for m in mem_res.data.records]

        # Read Trends & Metrics
        analytics = self.registry.ordered_candidates("social_analytics")[0]
        trend_res = await analytics.fetch(
            SocialAnalyticsInput(
                character_id=character_id,
                platform="instagram" if preferred_platform == "sandbox" else preferred_platform,  # type: ignore[arg-type]
                account_handle=f"@{character_id}",
                action="trends",
                niche_keywords=[profile.job, profile.goal[:30]],
            )
        )
        trends = [t.model_dump() for t in trend_res.data.trends]

        # Read Product Truth Registry (F19)
        products = list(
            session.execute(
                select(ProductItem).where(
                    ProductItem.character_id == character_id,
                    ProductItem.status == "active",
                )
            ).scalars()
        )
        latest_snap = session.execute(
            select(MetricSnapshot)
            .where(MetricSnapshot.character_id == character_id)
            .order_by(MetricSnapshot.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        # Real LangGraph StateGraph for Planner reasoning
        def node_evaluate_modes(state: PlannerGraphState) -> PlannerGraphState:
            sig_trends = state.get("trends", [])
            top_trend = sig_trends[0] if sig_trends else {"topic": "معرفی تخصصی", "growth_score": 0.7}
            prod_list = state.get("products", [])
            chosen_prod = prod_list[0] if prod_list else None
            growth_val = top_trend.get("growth_score", 0.0)
            growth_score = float(growth_val) if isinstance(growth_val, (int, float, str)) else 0.0

            # Select mode among trend_motion | new_idea | hybrid based on signals + directive
            if user_directive and ("ترند" in user_directive or "trend" in user_directive.lower()):
                mode = "trend_motion"
                reason = "درخواست مستقیم مدیر برای بررسی ترند و تطبیق با انتقال حرکت Wan Animate."
                rejected = [
                    {"mode": "new_idea", "reason": "اولویت با سوار شدن روی موج ترند درخواستی است"},
                    {"mode": "hybrid", "reason": "نیازی به ترکیب کلیپ چندمنبعی نیست"},
                ]
            elif growth_score >= 0.85 and not chosen_prod:
                mode = "trend_motion"
                reason = "امتیاز رشد ترند روز >= 0.85 و پنجره زمانی باز است؛ انتقال حرکت بیشترین بازده را دارد."
                rejected = [
                    {"mode": "new_idea", "reason": "نرخ رشد ترند فعلی شتاب اولیه بیشتری می‌دهد"},
                ]
            elif chosen_prod is not None:
                mode = "new_idea"
                reason = f"تمرکز مأموریت روی معرفی دقیق محصول تاییدشده ({chosen_prod['sku']}) بدون خطای قیمت است."
                rejected = [
                    {"mode": "trend_motion", "reason": "حالت محصول‌محور نیازمند بیان دقیق ویژگی و لینک خرید است"},
                ]
            else:
                mode = "hybrid"
                reason = "ترکیب کلیپ‌های کوتاه + هوک ۳ ثانیه‌ای برای آزمون ساختار با کمترین ریسک."
                rejected = [
                    {"mode": "trend_motion", "reason": "فقدان ویدیوی مرجع تاییدشده در لحظه"},
                ]

            topic = user_directive.strip() or str(top_trend.get("topic", "معرفی کاربردی روز"))
            if chosen_prod:
                topic = f"{topic} — {chosen_prod['name']}"

            task = TaskGraphItem(
                topic=topic[:160],
                format="reel_9_16",
                production_mode=mode,  # type: ignore[arg-type]
                platform=preferred_platform,  # type: ignore[arg-type]
                expected_kpi=state.get("latest_metrics") or profile.kpis,
                reason=reason,
                rejected_options=rejected,
                signals_used={
                    "trend_count": len(sig_trends),
                    "memories_used": len(state.get("memories", [])),
                    "product_sku": chosen_prod["sku"] if chosen_prod else None,
                    "user_directive": user_directive,
                },
                product_sku=chosen_prod["sku"] if chosen_prod else None,
            )
            dec = DecisionRecord(
                signals=task.signals_used,
                decision=f"mode={mode}; platform={preferred_platform}; topic={task.topic}",
                reason=reason,
                rejected_options=rejected,
                expected_kpi=task.expected_kpi,
            )
            return {"task_graph": [task.model_dump()], "decision_record": dec.model_dump(mode="json")}

        builder = StateGraph(PlannerGraphState)
        builder.add_node("evaluate_modes", node_evaluate_modes)
        builder.add_edge(START, "evaluate_modes")
        builder.add_edge("evaluate_modes", END)
        graph = builder.compile(checkpointer=self._checkpointer)

        out = graph.invoke(
            {
                "character_id": character_id,
                "goal": profile.goal,
                "job": profile.job,
                "tone": list(profile.tone),
                "memories": memories,
                "trends": trends,
                "products": [
                    {"sku": p.sku, "name": p.name, "price": p.price, "discount": p.discount}
                    for p in products
                ],
                "latest_metrics": {"views": latest_snap.views, "sales": latest_snap.sales_count}
                if latest_snap
                else {},
            },
            config={"configurable": {"thread_id": f"planner:{character_id}:{thread_id}"}},
        )

        # Optional LLM enrichment using Layer 1 profile + Layer 2 memories
        llm_res = await run_llm_with_failover(
            self.registry,
            LLMInput(
                messages=[
                    LLMMessage(
                        role="system",
                        content=f"Profile: {profile.name} | Goal: {profile.goal} | Dont: {profile.dont}",
                    ),
                    LLMMessage(
                        role="user",
                        content=f"Summarize editorial rationale for plan: {out['task_graph'][0]['topic']}",
                    ),
                ],
                task_complexity="light",
            ),
            session=session,
            character_id=character_id,
            pipeline="planner_agent",
        )

        dec_data = dict(out["decision_record"])
        dec_log = DecisionLog(
            character_id=character_id,
            signals=dec_data["signals"],
            decision=dec_data["decision"],
            reason=f"{dec_data['reason']} | {llm_res.data.text[:160]}",
            rejected_options=dec_data["rejected_options"],
            expected_kpi=dec_data["expected_kpi"],
        )
        session.add(dec_log)
        session.commit()
        session.refresh(dec_log)

        return {
            "decision_log_id": dec_log.id,
            "task_graph": out["task_graph"],
            "decision": dec_log.decision,
            "reason": dec_log.reason,
            "rejected_options": dec_log.rejected_options,
        }
