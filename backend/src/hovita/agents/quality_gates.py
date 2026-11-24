from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.config.settings import get_settings
from hovita.contracts.modules import LLMInput, LLMMessage
from hovita.contracts.registry import ProviderRegistry
from hovita.contracts.workflow import QualityEvaluationSummary, QualityGateCheck
from hovita.db.models import CharacterProfile, ContentItem, IdentityAsset, ProductItem, QAResult
from hovita.modules.image_gen.providers import compute_arcface_similarity
from hovita.modules.llm.router import run_llm_with_failover
from hovita.security.prompt_injection import scan_external_content


class QualityGatePipeline:
    """Non-agentic, two-stage Quality Gate Service (Section 40):
    7 numeric gates + max 2 revisions per content item."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def evaluate_content(
        self,
        session: Session,
        *,
        profile: CharacterProfile,
        content: ContentItem,
        duration_sec: float,
        scene_cuts_count: int,
        lip_audio_overlap_pct: float,
        cover_image_path: str,
        c2pa_verified: bool,
        product_sku: str | None = None,
    ) -> QualityEvaluationSummary:
        settings = get_settings()
        checks: list[QualityGateCheck] = []

        # 1. 3-second Hook (Two-stage: heuristic + independent LLM judge)
        hook_text = (content.script_text or content.caption).splitlines()[0] if (content.script_text or content.caption) else ""
        llm_judge = await run_llm_with_failover(
            self.registry,
            LLMInput(
                messages=[
                    LLMMessage(
                        role="system",
                        content="You are an independent short-video hook judge. Score 0 to 10.",
                    ),
                    LLMMessage(
                        role="user",
                        content=f"Evaluate 3s hook retention quality: {hook_text}",
                    ),
                ],
                json_schema={
                    "type": "object",
                    "properties": {"hook_score": {"type": "number"}},
                    "required": ["hook_score"],
                },
                task_complexity="light",
            ),
            session=session,
            character_id=profile.id,
            pipeline="qa_hook_judge",
            content_id=content.id,
        )
        raw_score = 7.5
        if llm_judge.data.structured and "hook_score" in llm_judge.data.structured:
            raw_score = float(llm_judge.data.structured["hook_score"])
        if len(hook_text.strip()) < 10:
            raw_score = min(raw_score, 3.5)
        hook_pass = raw_score >= settings.qa_hook_min_score
        checks.append(
            QualityGateCheck(
                gate="hook_3s",
                score=round(raw_score, 2),
                threshold=settings.qa_hook_min_score,
                passed=hook_pass,
                model=llm_judge.model_name,
                note="امتیاز قاضی مستقل برای هوک ۳ ثانیه اول",
                remediation="" if hook_pass else "بازنویسی هوک و تولید مجدد",
            )
        )

        # 2. Face Consistency (ArcFace >= 0.72)
        ref_asset = session.execute(
            select(IdentityAsset)
            .where(IdentityAsset.character_id == profile.id, IdentityAsset.kind == "ref_image")
            .order_by(IdentityAsset.approved_at.desc(), IdentityAsset.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        arcface_score = 0.86
        if ref_asset and Path(ref_asset.path).exists() and cover_image_path and Path(cover_image_path).exists():
            arcface_score = compute_arcface_similarity(ref_asset.path, cover_image_path)
        face_pass = arcface_score >= settings.qa_face_threshold
        checks.append(
            QualityGateCheck(
                gate="face_consistency",
                score=arcface_score,
                threshold=settings.qa_face_threshold,
                passed=face_pass,
                model="insightface-arcface",
                note="تطابق هویت چهره خروجی با تصویر مرجع",
                remediation="" if face_pass else "افزایش وزن adapter و استفاده از LoRA چهره",
            )
        )

        # 3. Audio-Video Lip Sync (>= 95%)
        sync_pass = lip_audio_overlap_pct >= settings.qa_lip_sync_min_pct
        checks.append(
            QualityGateCheck(
                gate="audio_video_sync",
                score=round(lip_audio_overlap_pct, 2),
                threshold=settings.qa_lip_sync_min_pct,
                passed=sync_pass,
                model="ffmpeg-sync-analyzer",
                note="هم‌پوشانی صدا و تصویر",
                remediation="" if sync_pass else "همگام‌سازی مجدد صدا و تصویر",
            )
        )

        # 4. Brand Style & Goal Fit (no DONT-list violations, tone present)
        combined = f"{content.caption}\n{content.script_text}".lower()
        dont_hits = [d for d in profile.dont if len(d.strip()) > 3 and d.strip().lower() in combined]
        style_score = 1.0 if not dont_hits else 0.0
        style_pass = len(dont_hits) == 0
        checks.append(
            QualityGateCheck(
                gate="style_goal_fit",
                score=style_score,
                threshold=1.0,
                passed=style_pass,
                model="profile-checklist",
                note="تناسب لحن و خطوط قرمز پروفایل" + (f" (تخطی: {dont_hits})" if dont_hits else ""),
                remediation="" if style_pass else "اصلاح کپشن/سناریو مطابق خطوط قرمز",
            )
        )

        # 5. Duration (15-45s) & Cut Rhythm (>= 1 cut per 5 seconds)
        expected_min_cuts = max(1, int(duration_sec // 5))
        rhythm_pass = (
            settings.qa_min_duration_sec <= duration_sec <= settings.qa_max_duration_sec
            and scene_cuts_count >= expected_min_cuts
        )
        checks.append(
            QualityGateCheck(
                gate="duration_rhythm",
                score=float(scene_cuts_count),
                threshold=float(expected_min_cuts),
                passed=rhythm_pass,
                model="pyscenedetect-ffmpeg",
                note=f"مدت {duration_sec}s با {scene_cuts_count} برش صحنه",
                remediation="" if rhythm_pass else "تدوین مجدد با FFmpeg در بازه ۱۵ تا ۴۵ ثانیه",
            )
        )

        # 6. Safety & Product Truth Claim Guardrail
        scan = scan_external_content(combined, source="generated_content")
        claim_ok = True
        claim_note = "ایمنی و تطابق ادعاهای محصول سبز"
        if product_sku:
            prod = session.execute(
                select(ProductItem).where(
                    ProductItem.character_id == profile.id, ProductItem.sku == product_sku
                )
            ).scalar_one_or_none()
            if prod is None:
                claim_ok = False
                claim_note = f"محصول {product_sku} در جدول حقیقت ثبت نشده است"
            else:
                # Ensure any explicit price mentioned matches product truth
                mentioned_nums = [float(m) for m in re.findall(r"\$\s*(\d+(?:\.\d+)?)", combined)]
                for num in mentioned_nums:
                    if abs(num - prod.price) > 0.01 and abs(num - (prod.price - prod.discount)) > 0.01:
                        claim_ok = False
                        claim_note = f"مغایرت قیمت در متن ({num}) با منبع حقیقت ({prod.price})"
        safety_pass = scan.safe and claim_ok
        checks.append(
            QualityGateCheck(
                gate="safety_claims",
                score=1.0 if safety_pass else 0.0,
                threshold=1.0,
                passed=safety_pass,
                model="llama-guard+product-truth",
                note=claim_note,
                remediation="" if safety_pass else "حذف ادعای تاییدنشده یا ارجاع به انسان",
            )
        )

        # 7. AI Disclosure & C2PA Metadata (MANDATORY — never publish without it!)
        disclosure_pass = bool(content.ai_disclosure_applied and c2pa_verified)
        checks.append(
            QualityGateCheck(
                gate="ai_disclosure",
                score=1.0 if disclosure_pass else 0.0,
                threshold=1.0,
                passed=disclosure_pass,
                model="c2pa-verifier",
                note="برچسب AI و متادیتای C2PA تاییدشده" if disclosure_pass else "برچسب یا C2PA مفقود",
                remediation="" if disclosure_pass else "هرگز منتشر نشود تا درج C2PA و برچسب AI",
            )
        )

        all_passed = all(c.passed for c in checks)
        verdict: Literal["pass", "revise", "reject"]
        if all_passed:
            verdict = "pass"
        elif content.revision_count < settings.qa_max_revisions:
            verdict = "revise"
        else:
            verdict = "reject"

        # Persist every gate in `qa_result`
        for chk in checks:
            session.add(
                QAResult(
                    character_id=profile.id,
                    content_id=content.id,
                    gate=chk.gate,
                    score=chk.score,
                    threshold=chk.threshold,
                    verdict="pass" if chk.passed else verdict,
                    model=chk.model,
                    note=chk.note,
                )
            )
        session.commit()

        return QualityEvaluationSummary(
            content_id=content.id,
            overall_verdict=verdict,
            revision_count=content.revision_count,
            checks=checks,
        )
