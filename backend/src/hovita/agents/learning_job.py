from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.contracts.modules import MemoryWriteInput
from hovita.contracts.registry import ProviderRegistry
from hovita.db.models import DecisionLog, MemoryEntry, MetricSnapshot
from hovita.db.repositories import assert_single_persona_id
from hovita.modules.memory.providers import ScopedVectorMemoryProvider
from hovita.security.audit import append_audit_log


class LearningLoopJob:
    """Non-agentic Service #2 (F14): Weekly analytics comparison, high-views-vs-low-sales
    discrepancy detection, plan adjustment, and 3x-conflict memory invalidation."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def run_weekly_learning_cycle(
        self, session: Session, *, character_id: str
    ) -> dict[str, Any]:
        profile = assert_single_persona_id(session, character_id)
        since = datetime.now(UTC) - timedelta(days=7)

        snaps = list(
            session.execute(
                select(MetricSnapshot).where(
                    MetricSnapshot.character_id == character_id,
                    MetricSnapshot.created_at >= since,
                )
            ).scalars()
        )
        total_views = sum(s.views for s in snaps)
        total_likes = sum(s.likes for s in snaps)
        total_saves = sum(s.saves for s in snaps)
        total_sales = sum(s.sales_count for s in snaps)
        total_revenue = round(sum(s.revenue_usd for s in snaps), 2)

        discrepancy_detected = False
        discrepancy_note = ""
        if total_views >= 2000 and total_sales == 0:
            discrepancy_detected = True
            discrepancy_note = (
                "هشدار عملکرد: بازدید بالا بدون تبدیل فروش — هوک سرگرم‌کننده است اما CTA "
                "و معرفی مزیت محصول یا لینک خرید در ۳ ثانیه اول/انتها ضعیف است."
            )
        elif profile.job == "seller" and total_views >= 1000 and (total_sales / max(total_views, 1)) < 0.001:
            discrepancy_detected = True
            discrepancy_note = (
                "نسبت فروش به بازدید کمتر از آستانه ۰٫۱٪ است؛ تمرکز برنامه هفته آینده روی مقایسه قیمت و اعتمادسازی می‌رود."
            )

        plan_change = (
            "تغییر برنامه هفته آینده: افزایش سهم ویدیوهای دمو و لینک مستقیم محصول از ۳۰٪ به ۶۰٪"
            if discrepancy_detected
            else "ادامه ریتم فعلی با افزایش ۲۰٪ انتقال حرکت ترند و حفظ هوک ۳ ثانیه‌ای"
        )

        # Store lesson into Layer 2 / Layer 4 memory
        mem = self.registry.ordered_candidates("memory")[0]
        await mem.write(
            MemoryWriteInput(
                character_id=character_id,
                kind="episode",
                semantic_key="weekly_learning_summary",
                owner_agent="learning_job",
                source="weekly_analytics",
                content=(
                    f"گزارش هفتگی: بازدید={total_views}، فروش={total_sales}، درآمد=${total_revenue}. "
                    f"{plan_change}"
                ),
            )
        )

        # If discrepancy detected, check if any active preference/fact conflicts with reality
        invalidated_memories: list[str] = []
        if discrepancy_detected and isinstance(mem, ScopedVectorMemoryProvider):
            active_mems = list(
                session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.character_id == character_id,
                        MemoryEntry.status == "active",
                        MemoryEntry.kind == "preference",
                    )
                ).scalars()
            )
            for m in active_mems:
                if "entertainment_only" in m.semantic_key or "no_cta" in m.semantic_key:
                    updated = mem.record_reality_conflict(
                        session,
                        character_id=character_id,
                        memory_id=m.id,
                        note="high_views_zero_sales_conflict",
                    )
                    if updated.status == "invalidated":
                        invalidated_memories.append(updated.id)

        dec = DecisionLog(
            character_id=character_id,
            signals={
                "views_7d": total_views,
                "likes_7d": total_likes,
                "saves_7d": total_saves,
                "sales_7d": total_sales,
                "revenue_7d": total_revenue,
                "discrepancy_detected": discrepancy_detected,
            },
            decision=plan_change,
            reason=discrepancy_note or "بهینه‌سازی تدریجی بر اساس متریک واقعی هفته گذشته",
            rejected_options=[{"option": "no_plan_change", "reason": "باید حلقه بازخورد اعمال شود"}],
            expected_kpi={"views_next_7d": int(total_views * 1.15), "sales_next_7d": max(1, total_sales + 1)},
        )
        session.add(dec)
        session.commit()

        append_audit_log(
            session,
            character_id=character_id,
            actor="learning_job",
            action="weekly_learning_report_generated",
            resource_type="decision_log",
            resource_id=dec.id,
            details={"discrepancy_detected": discrepancy_detected, "plan_change": plan_change},
        )
        return {
            "character_id": character_id,
            "period_days": 7,
            "metrics": {
                "views": total_views,
                "likes": total_likes,
                "saves": total_saves,
                "sales": total_sales,
                "revenue_usd": total_revenue,
            },
            "discrepancy_detected": discrepancy_detected,
            "discrepancy_note": discrepancy_note,
            "plan_change": plan_change,
            "invalidated_memories": invalidated_memories,
            "decision_log_id": dec.id,
        }
