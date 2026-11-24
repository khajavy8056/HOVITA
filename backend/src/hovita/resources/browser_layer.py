from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.config.settings import get_settings
from hovita.db.models import EmergencyState
from hovita.security.audit import append_audit_log
from hovita.security.emergency import assert_not_emergency_stopped


class BrowserPolicyViolation(PermissionError):
    """Raised whenever code attempts forbidden browser automation (auto-login/publish/CAPTCHA)."""


class BrowserActionRequest(BaseModel):
    action: Literal[
        "read_analyze",
        "visual_review_screenshot",
        "manual_human_session",
        "auto_login_publish",
        "auto_interact",
        "captcha_bypass",
    ]
    target_url: str
    human_approved: bool = False
    simulate_human_click: bool = False
    enter_credentials: bool = False


class BrowserActionResult(BaseModel):
    ok: bool
    action: str
    target_url: str
    daily_reads_used: int
    daily_read_limit: int
    screenshot_path: str | None = None
    notes: str = ""


class BrowserLayer:
    """Dedicated persistent-profile browser layer (Section 35 / Rule 6).

    Permitted ONLY for:
      1) Read & Analyze (low-rate public pages/trends/competitors)
      2) Visual Review & QA Screenshots
      3) Manual Human Session (e.g., highlights/bio edits by human)
    Structurally FORBIDS automated login/password publishing, automated engagement,
    human-click spoofing, or automated CAPTCHA bypass.
    """

    FORBIDDEN_ACTIONS: frozenset[str] = frozenset(
        {"auto_login_publish", "auto_interact", "captcha_bypass"}
    )

    def __init__(self, profile_dir: Path | None = None) -> None:
        settings = get_settings()
        self.profile_dir = (
            profile_dir or settings.local_storage_dir / "browser_profile"
        ).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._daily_reads = 0
        self._read_date = datetime.now(UTC).date()

    def _reset_daily_if_needed(self) -> None:
        today = datetime.now(UTC).date()
        if today != self._read_date:
            self._read_date = today
            self._daily_reads = 0

    def execute_action(
        self,
        session: Session,
        *,
        character_id: str,
        req: BrowserActionRequest,
    ) -> BrowserActionResult:
        settings = get_settings()
        assert_not_emergency_stopped(session, f"browser:{req.action}")

        em = session.execute(select(EmergencyState)).scalar_one_or_none()
        if em is not None and not em.browser_enabled:
            raise BrowserPolicyViolation("Browser layer is disabled in installation settings.")
        if not settings.browser_layer_enabled:
            raise BrowserPolicyViolation("Browser layer is disabled via configuration.")

        if req.action in self.FORBIDDEN_ACTIONS or req.enter_credentials or req.simulate_human_click:
            append_audit_log(
                session,
                character_id=character_id,
                actor="browser_layer",
                action="browser_policy_violation_blocked",
                resource_type="browser_session",
                resource_id=req.target_url,
                details={
                    "attempted_action": req.action,
                    "enter_credentials": req.enter_credentials,
                    "simulate_human_click": req.simulate_human_click,
                },
            )
            raise BrowserPolicyViolation(
                f"Forbidden browser automation blocked: '{req.action}'. "
                "Official Graph API / TikTok Content Posting API must be used for publishing."
            )

        self._reset_daily_if_needed()
        if self._daily_reads >= settings.browser_daily_read_limit:
            raise BrowserPolicyViolation(
                f"Daily browser read limit reached ({settings.browser_daily_read_limit})."
            )

        self._daily_reads += 1
        screenshot_path: str | None = None
        if req.action == "visual_review_screenshot":
            out = self.profile_dir / f"review_{int(datetime.now(UTC).timestamp())}.txt"
            out.write_text(f"Visual review placeholder for {req.target_url}\n", encoding="utf-8")
            screenshot_path = str(out)

        append_audit_log(
            session,
            character_id=character_id,
            actor="browser_layer",
            action=f"browser_{req.action}",
            resource_type="browser_session",
            resource_id=req.target_url,
            details={"daily_reads_used": self._daily_reads},
        )
        return BrowserActionResult(
            ok=True,
            action=req.action,
            target_url=req.target_url,
            daily_reads_used=self._daily_reads,
            daily_read_limit=settings.browser_daily_read_limit,
            screenshot_path=screenshot_path,
            notes="Completed under read-only/manual browser policy.",
        )
