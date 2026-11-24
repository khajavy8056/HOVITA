from __future__ import annotations

import io
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.orm import Session

from hovita.agents.identity_studio import IdentityStudio
from hovita.agents.onboarding import (
    ConversationalPersonaBuilder,
    evaluate_job_kpi,
    generate_behavioral_style_samples,
)
from hovita.contracts.modules import TTSInput
from hovita.db.models import CharacterProfile
from hovita.modules.bootstrap import build_default_registry
from hovita.modules.video_edit.providers import inspect_mp4_metadata

CID = "persona-test-001"


def test_f01_conversational_builder_under_15_questions(db_session: Session) -> None:
    builder = ConversationalPersonaBuilder()
    r1 = builder.process_turn(
        db_session,
        character_id=CID,
        thread_id="t-f01",
        user_message="یک شخصیت فروشنده عطر جوان برای اینستاگرام و تیک‌تاک بساز با بودجه ۶۰ دلار",
    )
    assert not r1.completed
    assert r1.question_count < 15
    assert r1.next_question is not None

    # Supply remaining answers in multi-turn flow
    r2 = builder.process_turn(
        db_session,
        character_id=CID,
        thread_id="t-f01",
        user_message="اسمش لیلی راد باشه، ۲۵ ساله با موی موج‌دار مشکی و استایل شیک، لحنش صمیمی و شفاف",
        explicit_fields={
            "name": "لیلی راد",
            "appearance_desc": "۲۵ ساله، موی موج‌دار مشکی، چهره طبیعی و استایل شیک",
            "tone_desc": "صمیمی، شفاف، حرفه‌ای بدون اغراق",
        },
    )
    assert r2.completed
    assert r2.question_count < 15
    assert r2.profile is not None
    assert r2.brief is not None
    assert r2.profile.job == "seller"
    assert r2.profile.name == "لیلی راد"
    assert r2.brief.monthly_budget_usd == 60.0


def test_f04_and_f05_behavioral_samples_and_job_kpi(db_session: Session) -> None:
    profile = db_session.get(CharacterProfile, CID)
    assert profile is not None
    profile.job = "seller"
    profile.dont = ["ادعای دروغین", "واترمارک"]
    db_session.commit()

    samples = generate_behavioral_style_samples(profile)
    assert len(samples) == 5
    assert all(s["passed_style_gate"] for s in samples)

    seller_eval = evaluate_job_kpi("seller", {"views": 5000, "sales_count": 4, "revenue_usd": 200})
    assert seller_eval["primary_metric"] == "sales_count"
    assert seller_eval["passed"] is True

    inf_eval = evaluate_job_kpi("influencer", {"views": 5000, "followers_delta": 120})
    assert inf_eval["primary_metric"] == "views_and_growth"
    assert inf_eval["passed"] is True


@pytest.mark.asyncio
async def test_f02_f03_f06_identity_voice_and_intro_video(db_session: Session) -> None:
    reg = build_default_registry()
    studio = IdentityStudio(reg)

    # F02: Multi-angle + refinement + LoRA approval with ArcFace >= 0.72
    res = await studio.generate_multi_angle_identity(
        db_session,
        character_id=CID,
        refinement_note="موها فرتر، بینی ثابت",
        seed=77,
        approve=True,
    )
    assert res["approved"] is True
    assert len(res["angles"]) == 6
    assert res["all_passed"] is True
    assert res["min_arcface_score"] >= 0.72
    assert Path(res["lora_path"]).exists()

    # F03: Voice audition + cloning + 3 diverse outputs share exact same voice_id
    vres = await studio.audition_and_lock_voice(
        db_session, character_id=CID, language="fa", selected_energy="balanced", approve=True
    )
    assert len(vres["candidates"]) == 3
    vid = vres["voice_id"]
    tts = reg.ordered_candidates("tts")[0]
    for idx, txt in enumerate(
        [
            "معرفی محصول اول با صدای تثبیت‌شده",
            "نکته آموزشی کوتاه درباره عطرهای ماندگار",
            "دعوت به اقدام شفاف برای دنبال‌کنندگان",
        ]
    ):
        out = await tts.synthesize(
            TTSInput(character_id=CID, text=f"{txt} {idx}", voice_id=vid, language="fa")
        )
        assert out.data.voice_id == vid
        assert Path(out.data.file_path).exists()

    # F06: Intro preview MP4 with C2PA
    prev = await studio.generate_intro_preview_video(db_session, character_id=CID)
    mp4 = Path(prev["preview_mp4_path"])
    assert mp4.exists()
    assert mp4.stat().st_size > 1000
    assert prev["c2pa_verified"] is True
    meta = inspect_mp4_metadata(mp4)
    assert meta["c2pa_verified"] is True


def test_f18_f19_environment_and_product_truth_import(db_session: Session) -> None:
    reg = build_default_registry()
    studio = IdentityStudio(reg)

    env1 = studio.register_environment_pack(
        db_session,
        character_id=CID,
        kind="real",
        capture_method="multi_angle_photos",
        media=["shop_front.jpg", "shelf_01.jpg"],
        palette=["#1D2D44", "#F4EFE6"],
        fixed_props=["چوب گردو", "نور گرم"],
        product_zones=["میز جلو"],
    )
    assert env1.approved_version == 1

    # Import XLSX catalog (F19)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["sku", "name", "brand", "price", "discount", "stock", "features", "buy_link"])
    ws.append(["PRF-101", "عطر شب لیلی", "Hovita", 65.0, 5.0, 20, "100ml EDP", "https://shop.local/prf-101"])
    buf = io.BytesIO()
    wb.save(buf)

    items = studio.import_products_csv_or_xlsx(
        db_session,
        character_id=CID,
        filename="catalog.xlsx",
        raw_bytes=buf.getvalue(),
    )
    assert len(items) == 1
    assert items[0].sku == "PRF-101"
    assert items[0].price == 65.0
    assert items[0].source == "xlsx"
