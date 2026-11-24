from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.config.settings import get_settings
from hovita.db.models import (
    CharacterProfile,
    EmergencyState,
    OnboardingProgress,
)

ONBOARDING_STEPS: list[tuple[int, str, str]] = [
    (1, "persona_core", "تعریف هویت و شخصیت پایه"),
    (2, "visual_identity", "تثبیت چهره چندزاویه‌ای و LoRA"),
    (3, "voice_identity", "انتخاب و کلون صدای اختصاصی"),
    (4, "social_connect", "اتصال رسمی اینستاگرام و تیک‌تاک"),
    (5, "page_audit", "آنالیز وضعیت فعلی پیج"),
    (6, "market_competitors", "تحلیل بازار، مخاطب و رقبا"),
    (7, "content_style", "تعیین سبک، فرمت و نیازهای اولیه پیج"),
    (8, "initial_plan", "تولید برنامه محتوایی ۲ هفته‌ای اولیه"),
]


class SinglePersonaViolationError(RuntimeError):
    """Raised when attempting to violate the single-persona-per-install invariant."""


def assert_single_persona_id(session: Session, character_id: str) -> CharacterProfile:
    """Verify the requested character_id matches this installation's sole persona."""
    row = session.execute(select(CharacterProfile)).scalar_one_or_none()
    if row is None:
        raise SinglePersonaViolationError(
            "Installation has no seeded character_profile yet."
        )
    if row.id != character_id:
        raise SinglePersonaViolationError(
            f"Single-persona violation: installation owns '{row.id}', got '{character_id}'. "
            "Multiple personas require cloning a separate installation."
        )
    return row


def seed_installation(session: Session) -> CharacterProfile:
    """Idempotently initialize the sole persona, emergency state, and 8-step setup tracker."""
    settings = get_settings()
    profile = session.execute(select(CharacterProfile)).scalar_one_or_none()
    if profile is None:
        profile = CharacterProfile(
            id=settings.character_id,
            singleton_lock=1,
            name=settings.character_name,
            status="draft",
            job="influencer",
            goal="رشد ارگانیک و تولید محتوای پایدار با هویت ثابت",
            kpis={"views_30d": 100000, "followers_delta": 5000, "engagement_rate_min": 0.05},
            audience="مخاطبان فارسی‌زبان و علاقه‌مندان سبک زندگی و فناوری",
            appearance={
                "age": "26",
                "hair": "قهوه‌ای تیره موج‌دار",
                "style": "کژوال شیک و مدرن",
                "eyes": "قهوه‌ای روشن",
            },
            locked_physical_prompt=(
                "26-year-old Persian studio creator, warm olive skin, dark brown wavy hair, "
                "natural friendly expression, smart-casual neutral outfit, soft cinematic daylight"
            ),
            tone=["صمیمی", "حرفه‌ای", "انرژیک", "شفاف"],
            do=["هوک ۳ ثانیه اول قوی", "شفافیت درباره هویت هوش مصنوعی", "پاسخ مستند"],
            dont=["وعده‌های غیرواقعی", "تقلید چهره افراد واقعی", "واترمارک"],
            humor_level="subtle",
            language="fa",
            autonomy_level=settings.autonomy_level,
            publish_mode=settings.publish_mode,
            version=1,
        )
        session.add(profile)
        session.flush()

    emergency = session.execute(select(EmergencyState)).scalar_one_or_none()
    if emergency is None:
        emergency = EmergencyState(
            id="global",
            singleton_lock=1,
            character_id=profile.id,
            kill_switch_active=False,
            reason="",
            triggered_by="",
            browser_enabled=settings.browser_layer_enabled,
            gpu_mode=settings.gpu_mode,
        )
        session.add(emergency)

    existing_steps = {
        r.step_index
        for r in session.execute(
            select(OnboardingProgress).where(OnboardingProgress.character_id == profile.id)
        ).scalars()
    }
    for idx, key, title in ONBOARDING_STEPS:
        if idx not in existing_steps:
            session.add(
                OnboardingProgress(
                    character_id=profile.id,
                    step_index=idx,
                    step_key=key,
                    title=title,
                    status="pending",
                    pct=0,
                    output_json={},
                )
            )
    session.commit()
    session.refresh(profile)
    return profile
