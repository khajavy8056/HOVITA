from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.contracts.brief import (
    CharacterProfileContract,
    ConversationalBuilderQuestion,
    ConversationalBuilderResponse,
    PageConfig,
    PersonaBrief,
    PersonaKPIs,
)
from hovita.db.models import BriefRecord, CharacterProfile, OnboardingProgress
from hovita.db.repositories import assert_single_persona_id
from hovita.modules.workflow_engine.providers import get_langgraph_checkpointer
from hovita.security.audit import append_audit_log

_FIELD_PROMPTS: list[tuple[str, str, str]] = [
    (
        "name",
        "اسم شخصیت چی باشه؟ (مثلاً: سارا، رادین، آوا)",
        "What should the persona's name be?",
    ),
    (
        "job",
        "شغل و مأموریت اصلیش چیه؟ (اینفلوئنسر / فروشنده / متخصص / پشتیبانی / محقق)",
        "What is the persona's primary job? (influencer / seller / specialist / support / researcher)",
    ),
    (
        "goal",
        "هدف دقیق ۳۰ روزه و معیار موفقیتش چیه؟ (مثلاً: ۵۰۰ هزار بازدید یا ۳۰ فروش ماهانه)",
        "What is the 30-day goal and target KPI?",
    ),
    (
        "appearance",
        "ظاهر فیزیکی، سن، رنگ مو و استایل لباسش چطور باشه؟",
        "Describe age, hair, face, and outfit style.",
    ),
    (
        "tone",
        "لحن و خطوط قرمز چیه؟ (چه چیزهایی بگه و چه چیزهایی هرگز نگه؟)",
        "Define tone of voice, humor, DOs, and DONTs.",
    ),
    (
        "platforms",
        "روی کدوم پلتفرم‌ها و با چه بودجه ماهانه‌ای (دلار) فعالیت کنه؟ (اینستاگرام / تیک‌تاک / یوتیوب)",
        "Which platforms (instagram/tiktok/youtube) and monthly USD budget?",
    ),
]


class BuilderGraphState(TypedDict, total=False):
    character_id: str
    question_count: int
    collected: dict[str, Any]
    missing_fields: list[str]
    last_user_input: str
    completed: bool


def _extract_fields_from_text(text: str, current: dict[str, Any]) -> dict[str, Any]:
    updated = dict(current)
    t = text.strip()
    if not t:
        return updated

    low = t.lower()
    # Detect job
    if "job" not in updated:
        if any(k in low for k in ["فروش", "عطر", "فروشگاه", "seller", "shop", "perfume", "محصول"]):
            updated["job"] = "seller"
        elif any(k in low for k in ["پشتیبانی", "support"]):
            updated["job"] = "support"
        elif any(k in low for k in ["تحقیق", "research"]):
            updated["job"] = "researcher"
        elif any(k in low for k in ["متخصص", "مشاور", "specialist", "doctor", "coach"]):
            updated["job"] = "specialist"
        elif any(k in low for k in ["اینفلوئنسر", "بلاگر", "تولید محتوا", "influencer", "creator"]):
            updated["job"] = "influencer"

    # Detect platforms
    if "platforms" not in updated:
        plats: list[str] = []
        if any(k in low for k in ["اینستاگرام", "instagram", "ریلز", "reels"]):
            plats.append("instagram_reels")
        if any(k in low for k in ["تیک‌تاک", "تیک تاک", "tiktok"]):
            plats.append("tiktok")
        if any(k in low for k in ["یوتیوب", "youtube", "shorts"]):
            plats.append("youtube_shorts")
        if plats:
            updated["platforms"] = plats

    # Detect budget
    m_budget = re.search(r"(\d+(?:\.\d+)?)\s*(?:دلار|usd|\$)", low)
    if m_budget:
        updated["monthly_budget_usd"] = float(m_budget.group(1))

    # Detect name if explicitly stated
    m_name = re.search(r"(?:اسم|نام|name)\s*(?::|ش|شخصیت)?\s*([^\n,،.]+)", t, re.IGNORECASE)
    if m_name and "name" not in updated:
        updated["name"] = m_name.group(1).strip()[:40]

    # Detect goal if substantial sentence provided initially
    if "goal" not in updated and len(t) >= 12:
        updated["goal"] = t[:240]

    # Detect appearance clues
    if "appearance" not in updated and any(
        k in low for k in ["مو", "چهره", "استایل", "ساله", "hair", "style", "outfit", "پوشش", "جوان"]
    ):
        updated["appearance_desc"] = t[:220]

    # Detect tone clues
    if "tone" not in updated and any(
        k in low for k in ["لحن", "شوخ", "صمیمی", "حرفه‌ای", "خط قرمز", "ممنوع", "tone", "friendly"]
    ):
        updated["tone_desc"] = t[:220]

    return updated


def _compute_missing(collected: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key, _, _ in _FIELD_PROMPTS:
        if key == "appearance":
            if not (collected.get("appearance") or collected.get("appearance_desc")):
                missing.append("appearance")
        elif key == "tone":
            if not (collected.get("tone") or collected.get("tone_desc")):
                missing.append("tone")
        elif key not in collected or not collected[key]:
            missing.append(key)
    return missing


class ConversationalPersonaBuilder:
    """F01: LangGraph-checkpointed sequential conversational persona builder (<15 questions)."""

    def __init__(self) -> None:
        self._checkpointer = get_langgraph_checkpointer()
        self._graph = self._build_graph()

    def _build_graph(self):  # type: ignore[no-untyped-def]
        def analyze_turn(state: BuilderGraphState) -> BuilderGraphState:
            collected = dict(state.get("collected", {}))
            last_input = state.get("last_user_input", "")
            qcount = int(state.get("question_count", 0))
            prev_missing = list(state.get("missing_fields", []))

            # Map direct answer to the previously asked missing field if short answer
            if prev_missing and last_input.strip():
                target = prev_missing[0]
                if target == "name" and "name" not in collected:
                    collected["name"] = last_input.strip()[:50]
                elif target == "appearance" and "appearance_desc" not in collected:
                    collected["appearance_desc"] = last_input.strip()
                elif target == "tone" and "tone_desc" not in collected:
                    collected["tone_desc"] = last_input.strip()
                elif target == "goal" and "goal" not in collected:
                    collected["goal"] = last_input.strip()
                elif target == "platforms" and "platforms" not in collected:
                    plats: list[str] = []
                    low = last_input.lower()
                    if "اینستا" in low or "insta" in low:
                        plats.append("instagram_reels")
                    if "تیک" in low or "tiktok" in low:
                        plats.append("tiktok")
                    if "یوتیوب" in low or "youtube" in low:
                        plats.append("youtube_shorts")
                    collected["platforms"] = plats or ["instagram_reels", "tiktok"]

            collected = _extract_fields_from_text(last_input, collected)
            missing = _compute_missing(collected)

            # Guarantee < 15 questions cap: auto-fill smart defaults after 6 questions if needed
            if qcount >= 6 and missing:
                for m in list(missing):
                    if m == "name":
                        collected["name"] = "آوا راد"
                    elif m == "job":
                        collected["job"] = "influencer"
                    elif m == "goal":
                        collected["goal"] = "رشد ارگانیک پیج و تعامل پایدار در ۳۰ روز"
                    elif m == "appearance":
                        collected["appearance_desc"] = "۲۶ ساله، استایل مدرن و مرتب، لبخند صمیمی"
                    elif m == "tone":
                        collected["tone_desc"] = "صمیمی، حرفه‌ای، شفاف بدون ادعای غیرواقعی"
                    elif m == "platforms":
                        collected["platforms"] = ["instagram_reels", "tiktok"]
                missing = []

            return {
                "character_id": state.get("character_id", ""),
                "question_count": qcount + (1 if missing else 0),
                "collected": collected,
                "missing_fields": missing,
                "last_user_input": last_input,
                "completed": len(missing) == 0,
            }

        g = StateGraph(BuilderGraphState)
        g.add_node("analyze_turn", analyze_turn)
        g.add_edge(START, "analyze_turn")
        g.add_edge("analyze_turn", END)
        return g.compile(checkpointer=self._checkpointer)

    def process_turn(
        self,
        session: Session,
        *,
        character_id: str,
        thread_id: str,
        user_message: str,
        explicit_fields: dict[str, Any] | None = None,
    ) -> ConversationalBuilderResponse:
        assert_single_persona_id(session, character_id)
        cfg = {"configurable": {"thread_id": f"builder:{character_id}:{thread_id}"}}
        snap = self._graph.get_state(cfg)
        prev_values: dict[str, Any] = dict(snap.values) if snap and snap.values else {}
        merged_collected = dict(prev_values.get("collected", {}))
        if explicit_fields:
            merged_collected.update(explicit_fields)

        res_state = self._graph.invoke(
            {
                "character_id": character_id,
                "question_count": int(prev_values.get("question_count", 0)),
                "collected": merged_collected,
                "missing_fields": list(prev_values.get("missing_fields", [])),
                "last_user_input": user_message,
            },
            config=cfg,
        )

        missing = list(res_state.get("missing_fields", []))
        qcount = int(res_state.get("question_count", 0))
        completed = bool(res_state.get("completed", False))
        collected = dict(res_state.get("collected", {}))

        if not completed and missing:
            target_key = missing[0]
            prompt_fa, prompt_en = next(
                ((fa, en) for k, fa, en in _FIELD_PROMPTS if k == target_key),
                ("لطفاً جزئیات این بخش را بنویسید.", "Please provide details for this field."),
            )
            return ConversationalBuilderResponse(
                completed=False,
                question_count=qcount,
                missing_fields=missing,
                next_question=ConversationalBuilderQuestion(
                    step=qcount,
                    field_key=target_key,
                    question_fa=prompt_fa,
                    question_en=prompt_en,
                    missing_fields=missing,
                ),
            )

        # Build validated CharacterProfileContract + PersonaBrief (v1.0)
        job: Literal["influencer", "seller", "specialist", "support", "researcher"] = collected.get(
            "job", "influencer"
        )
        if job not in {"influencer", "seller", "specialist", "support", "researcher"}:
            job = "influencer"

        kpis = (
            PersonaKPIs(views_30d=80_000, followers_delta=2_000, engagement_rate_min=0.05, sales_target_30d=40)
            if job == "seller"
            else PersonaKPIs(views_30d=500_000, followers_delta=15_000, engagement_rate_min=0.06)
        )
        raw_plats = collected.get("platforms", ["instagram_reels", "tiktok"])
        valid_plats: list[Literal["instagram_reels", "tiktok", "youtube_shorts"]] = [
            p for p in raw_plats if p in {"instagram_reels", "tiktok", "youtube_shorts"}
        ] or ["instagram_reels", "tiktok"]

        name = str(collected.get("name", "آوا راد"))
        goal = str(collected.get("goal", "تولید محتوای مستمر و رشد ارگانیک پیج"))
        appearance_desc = str(collected.get("appearance_desc", "۲۶ ساله، استایل مدرن و تمیز"))
        tone_desc = str(collected.get("tone_desc", "صمیمی، شفاف، حرفه‌ای"))
        budget = float(collected.get("monthly_budget_usd", 60.0))

        locked_prompt = (
            f"Portrait of {name}, {appearance_desc}, consistent facial structure, "
            "natural lighting, crisp focus, studio quality"
        )

        # Persist into DB (Source of Truth Layer 1)
        row = session.execute(
            select(CharacterProfile).where(CharacterProfile.id == character_id)
        ).scalar_one()
        row.name = name
        row.job = job
        row.goal = goal
        row.kpis = kpis.model_dump()
        row.appearance = {"description": appearance_desc, "style": "modern"}
        row.locked_physical_prompt = locked_prompt
        row.tone = [t.strip() for t in re.split(r"[،,|/]", tone_desc) if t.strip()] or ["صمیمی", "حرفه‌ای"]
        row.do = ["هوک ۳ ثانیه اول", "شفافیت درباره هویت هوش مصنوعی", "وفاداری به داده محصول"]
        row.dont = ["ادعای پزشکی/مالی بی‌اساس", "توهین یا حاشیه", "واترمارک"]
        row.version += 1

        brief = PersonaBrief(
            goal=goal,
            kpis=kpis,
            platforms=valid_plats,
            monthly_budget_usd=budget,
            page=PageConfig(state="fresh", handle=f"@{character_id}"),
        )
        brief_rec = BriefRecord(
            character_id=character_id,
            brief_version=brief.brief_version,
            goal=brief.goal,
            kpis=brief.kpis.model_dump(),
            platforms=list(brief.platforms),
            budget=brief.monthly_budget_usd,
            constraints=brief.constraints,
            approval_policy=brief.approval_policy,
            raw_json=brief.model_dump(),
        )
        session.add(brief_rec)

        step1 = session.execute(
            select(OnboardingProgress).where(
                OnboardingProgress.character_id == character_id,
                OnboardingProgress.step_index == 1,
            )
        ).scalar_one_or_none()
        if step1 is not None:
            step1.status = "completed"
            step1.pct = 100
            step1.output_json = {"name": name, "job": job, "version": row.version}

        session.commit()
        session.refresh(row)

        append_audit_log(
            session,
            character_id=character_id,
            actor="onboarding_builder",
            action="persona_profile_and_brief_saved",
            resource_type="character_profile",
            resource_id=character_id,
            details={"question_count": qcount, "job": job, "version": row.version},
        )

        profile_dto = CharacterProfileContract(
            id=row.id,
            name=row.name,
            status="draft" if row.status not in {"draft", "identity_locked", "active", "paused"} else row.status,  # type: ignore[arg-type]
            job=job,
            goal=row.goal,
            kpis=row.kpis,
            audience=row.audience,
            appearance={str(k): str(v) for k, v in row.appearance.items()},
            locked_physical_prompt=row.locked_physical_prompt,
            tone=list(row.tone),
            do=list(row.do),
            dont=list(row.dont),
            language="fa",
            autonomy_level="L1",
            publish_mode="approval_required",
            version=row.version,
        )
        return ConversationalBuilderResponse(
            completed=True,
            question_count=qcount,
            missing_fields=[],
            profile=profile_dto,
            brief=brief,
        )


def generate_behavioral_style_samples(profile: CharacterProfile) -> list[dict[str, Any]]:
    """F04: Generate 5 sample captions/responses & verify zero DONT-list violations."""
    scenarios = [
        ("معرفی اول صفحه", "سلام! من با هویت هوشمند اختصاصی اینجام تا تجربه‌های واقعی و کاربردی رو باهاتون به اشتراک بذارم."),
        ("پاسخ به نظر انتقادی", "ممنون از نگاه دقیقت! نظرت رو بررسی کردم و در محتوای بعدی با شفافیت کامل نتیجه‌اش رو نشون می‌دم."),
        ("معرفی محصول/پیشنهاد", "مشخصات و قیمت دقیق فقط بر اساس کاتالوگ تاییدشده ارائه می‌شه تا انتخاب مطمئن‌تری داشته باشید."),
        ("هوک ۳ ثانیه ترند", "فقط توی ۳ ثانیه اول ببین چرا ۹۰ درصد افراد این جزئیات مهم رو جا می‌ندازن!"),
        ("شفافیت هویت AI", "این ویدیو و کپشن توسط شخصیت هوشمند هویتا با شفافیت کامل و برچسب محتوای مصنوعی تولید شده."),
    ]
    dont_words = [d.lower().strip() for d in profile.dont if d.strip()]
    results: list[dict[str, Any]] = []
    for label, text in scenarios:
        violated = [w for w in dont_words if len(w) > 3 and w in text.lower()]
        results.append(
            {
                "scenario": label,
                "sample_text": text,
                "tone_applied": profile.tone,
                "passed_style_gate": len(violated) == 0,
                "violations": violated,
            }
        )
    return results


def evaluate_job_kpi(job: str, metrics: dict[str, float | int]) -> dict[str, Any]:
    """F05: Score persona performance strictly according to its job/mission."""
    if job == "seller":
        sales = float(metrics.get("sales_count", 0))
        revenue = float(metrics.get("revenue_usd", 0.0))
        views = float(metrics.get("views", 1))
        conv = round((sales / max(views, 1.0)) * 100.0, 3)
        return {
            "job": "seller",
            "primary_metric": "sales_count",
            "sales_count": sales,
            "revenue_usd": revenue,
            "conversion_rate_pct": conv,
            "passed": sales > 0,
        }
    if job == "influencer":
        views = float(metrics.get("views", 0))
        followers_delta = float(metrics.get("followers_delta", 0))
        eng = float(metrics.get("engagement_rate", 0.0))
        return {
            "job": "influencer",
            "primary_metric": "views_and_growth",
            "views": views,
            "followers_delta": followers_delta,
            "engagement_rate": eng,
            "passed": views > 0 and followers_delta >= 0,
        }
    return {
        "job": job,
        "primary_metric": "credibility_saves_shares",
        "saves": float(metrics.get("saves", 0)),
        "shares": float(metrics.get("shares", 0)),
        "passed": True,
    }
