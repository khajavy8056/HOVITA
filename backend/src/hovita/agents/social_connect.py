from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.config.secrets import SecretStore
from hovita.contracts.modules import PublishInput, SocialAnalyticsInput
from hovita.contracts.registry import ProviderRegistry
from hovita.db.models import OnboardingProgress, SocialAccount
from hovita.db.repositories import assert_single_persona_id
from hovita.security.audit import append_audit_log


class SocialConnector:
    """M3: F07 Social Connect, F08 Page Audit, and F09 8-Step Launch Wizard."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    def connect_account(
        self,
        session: Session,
        *,
        character_id: str,
        platform: Literal["instagram", "tiktok", "youtube"],
        handle: str,
        access_token: str,
        scopes: list[str] | None = None,
        expires_in_days: int = 60,
    ) -> dict[str, Any]:
        """F07: Connect official platform account; store token ONLY in SecretStore."""
        assert_single_persona_id(session, character_id)
        if access_token.strip():
            SecretStore().set_provider_keys(platform, [access_token.strip()])

        default_scopes = {
            "instagram": ["instagram_basic", "instagram_content_publish", "pages_read_engagement"],
            "tiktok": ["video.publish", "video.upload", "user.info.basic"],
            "youtube": ["youtube.upload", "youtube.readonly"],
        }[platform]

        expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)
        acct = session.execute(
            select(SocialAccount).where(
                SocialAccount.character_id == character_id,
                SocialAccount.platform == platform,
            )
        ).scalar_one_or_none()
        if acct is None:
            acct = SocialAccount(
                character_id=character_id,
                platform=platform,
                handle=handle,
                auth_type="oauth2",
                scopes=scopes or default_scopes,
                token_expires_at=expires_at,
                status="connected",
                daily_publish_count=0,
            )
            session.add(acct)
        else:
            acct.handle = handle
            acct.scopes = scopes or default_scopes
            acct.token_expires_at = expires_at
            acct.status = "connected"

        step4 = session.execute(
            select(OnboardingProgress).where(
                OnboardingProgress.character_id == character_id,
                OnboardingProgress.step_index == 4,
            )
        ).scalar_one_or_none()
        if step4:
            step4.status = "completed"
            step4.pct = 100
            step4.output_json = {"platform": platform, "handle": handle}

        session.commit()
        session.refresh(acct)

        append_audit_log(
            session,
            character_id=character_id,
            actor="social_connector",
            action="social_account_connected",
            resource_type="social_account",
            resource_id=acct.id,
            details={"platform": platform, "handle": handle, "expires_at": expires_at.isoformat()},
        )
        return self.account_status(acct)

    def account_status(self, acct: SocialAccount) -> dict[str, Any]:
        now = datetime.now(UTC)
        exp = acct.token_expires_at
        hours_left = None
        expiring_soon = False
        if exp is not None:
            exp_aware = exp if exp.tzinfo else exp.replace(tzinfo=UTC)
            hours_left = round((exp_aware - now).total_seconds() / 3600.0, 1)
            expiring_soon = hours_left <= 24.0
        return {
            "id": acct.id,
            "platform": acct.platform,
            "handle": acct.handle,
            "status": acct.status,
            "scopes": acct.scopes,
            "daily_publish_count": acct.daily_publish_count,
            "token_expires_at": exp.isoformat() if exp else None,
            "hours_until_expiry": hours_left,
            "expiring_soon_warning": expiring_soon,
        }

    async def sandbox_test_publish(
        self,
        session: Session,
        *,
        character_id: str,
        media_path: str,
        caption: str = "انتشار آزمایشی سندباکس هویتا — شخصیت هوش مصنوعی",
    ) -> dict[str, Any]:
        assert_single_persona_id(session, character_id)
        pub = self.registry.ordered_candidates("publisher")[0]
        res = await pub.publish(
            PublishInput(
                character_id=character_id,
                content_id="sandbox-probe",
                platform="sandbox",
                media_path=media_path,
                caption=caption,
                hashtags=["#AI", "#Hovita"],
                ai_disclosure=True,
                idempotency_key=f"sandbox-probe-{int(datetime.now(UTC).timestamp())}",
            )
        )
        return res.data.model_dump()

    async def audit_page_and_run_setup_wizard(
        self,
        session: Session,
        *,
        character_id: str,
        platform: Literal["instagram", "tiktok", "youtube"] = "instagram",
        handle: str = "@hovita_persona",
        page_state: Literal["fresh", "existing"] = "existing",
    ) -> dict[str, Any]:
        """F08 + F09: Audit page (5 findings + 3 recommendations) and complete 8-step setup."""
        profile = assert_single_persona_id(session, character_id)
        analytics_provider = self.registry.ordered_candidates("social_analytics")[0]

        insights = await analytics_provider.fetch(
            SocialAnalyticsInput(
                character_id=character_id,
                platform=platform,
                account_handle=handle,
                action="page_insights",
            )
        )
        trends = await analytics_provider.fetch(
            SocialAnalyticsInput(
                character_id=character_id,
                platform=platform,
                account_handle=handle,
                action="trends",
                niche_keywords=[profile.job, profile.goal[:40]],
            )
        )

        snap = insights.data.page_snapshot
        posts_count = int(snap.get("posts_count", 0)) if page_state == "existing" else 0
        detected_state: Literal["fresh", "existing"] = "fresh" if posts_count == 0 else "existing"

        findings = [
            f"وضعیت پیج: {'خام (بدون پست قبلی)' if detected_state == 'fresh' else f'فعال با {posts_count} پست'}",
            f"نرخ تعامل میانگین فعلی: {snap.get('avg_engagement_rate', 0.0) * 100:.1f}%",
            f"ریتم انتشار کنونی: {snap.get('cadence_posts_per_week', 0)} پست در هفته",
            f"تعداد سیگنال ترند کشف‌شده در حوزه {profile.job}: {len(trends.data.trends)} مورد",
            "نیاز به هوک ۳ ثانیه اول قوی و برچسب‌گذاری شفاف هوش مصنوعی (C2PA + AI info)",
        ]
        recommendations = [
            "ساخت بسته هویت پیج (آواتار تثبیت‌شده، بایو شفاف AI، ۳ هایلایت ثابت) در هفته اول",
            "انتشار ۳ ریلز هفتگی با ریتم برش <= ۵ ثانیه و ترکیب ۶۰٪ ایده جدید + ۴۰٪ انتقال حرکت ترند",
            "پاسخ نیمه‌خودکار به کامنت‌های ماه اول و سنجش هفتگی KPI اختصاصی شغل",
        ]

        # Update Onboarding Steps 5..8 with structured outputs
        step_outputs: dict[int, dict[str, Any]] = {
            5: {"detected_state": detected_state, "findings": findings, "recommendations": recommendations},
            6: {
                "top_trends": [t.model_dump() for t in trends.data.trends[:3]],
                "audience": profile.audience,
            },
            7: {
                "tone": profile.tone,
                "formats": ["reel_9_16", "story"],
                "page_needs": ["avatar", "bio", "highlights", "cover_template"],
            },
            8: {
                "two_week_plan_posts": 6,
                "platforms": [platform],
                "approved": True,
            },
        }
        steps = list(
            session.execute(
                select(OnboardingProgress)
                .where(OnboardingProgress.character_id == character_id)
                .order_by(OnboardingProgress.step_index.asc())
            ).scalars()
        )
        for s in steps:
            if s.step_index in step_outputs:
                s.status = "completed"
                s.pct = 100
                s.output_json = step_outputs[s.step_index]
        session.commit()

        append_audit_log(
            session,
            character_id=character_id,
            actor="social_connector",
            action="page_audit_and_setup_completed",
            resource_type="onboarding_progress",
            resource_id=character_id,
            details={"detected_state": detected_state, "findings_count": len(findings)},
        )
        return {
            "character_id": character_id,
            "platform": platform,
            "handle": handle,
            "detected_state": detected_state,
            "findings": findings,
            "recommendations": recommendations,
            "steps": [
                {
                    "step_index": s.step_index,
                    "step_key": s.step_key,
                    "title": s.title,
                    "status": s.status,
                    "pct": s.pct,
                    "output": s.output_json,
                }
                for s in steps
            ],
        }
