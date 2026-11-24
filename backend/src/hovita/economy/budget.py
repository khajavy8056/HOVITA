from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from hovita.config.settings import get_settings
from hovita.economy.ledger import get_daily_spend_usd, get_monthly_spend_usd


class BudgetStatus(BaseModel):
    daily_spend_usd: float
    daily_budget_usd: float
    monthly_spend_usd: float
    utilization_pct: float
    warning_80pct: bool
    budget_exhausted: bool
    defer_non_critical: bool


class BudgetPolicy:
    """Enforces daily budget, 80% warning alert, deferral of non-critical work,
    and max 2 content revisions."""

    def evaluate(self, session: Session, character_id: str) -> BudgetStatus:
        settings = get_settings()
        daily = get_daily_spend_usd(session, character_id)
        monthly = get_monthly_spend_usd(session, character_id)
        budget = max(settings.daily_budget_usd, 0.0001)
        util = round(daily / budget, 4)
        warn = util >= settings.budget_warn_pct
        exhausted = daily >= settings.daily_budget_usd
        return BudgetStatus(
            daily_spend_usd=daily,
            daily_budget_usd=settings.daily_budget_usd,
            monthly_spend_usd=monthly,
            utilization_pct=round(util * 100.0, 2),
            warning_80pct=warn,
            budget_exhausted=exhausted,
            defer_non_critical=warn,
        )

    def can_run_job(
        self, session: Session, character_id: str, *, priority: str = "normal"
    ) -> tuple[bool, BudgetStatus]:
        status = self.evaluate(session, character_id)
        if status.budget_exhausted and priority != "critical":
            return False, status
        if status.warning_80pct and priority == "low":
            return False, status
        return True, status

    def can_revise_content(self, current_revisions: int) -> bool:
        settings = get_settings()
        return current_revisions < settings.qa_max_revisions
