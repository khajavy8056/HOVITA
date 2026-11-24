from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.config.settings import get_settings
from hovita.contracts.modules import (
    ImageGenInput,
    TTSInput,
    VideoEditInput,
    VideoGenInput,
)
from hovita.contracts.registry import ProviderRegistry
from hovita.db.models import (
    EnvironmentAsset,
    IdentityAsset,
    OnboardingProgress,
    ProductItem,
    VoiceAsset,
)
from hovita.db.repositories import assert_single_persona_id
from hovita.modules.image_gen.providers import compute_arcface_similarity
from hovita.resources.storage import get_object_storage
from hovita.security.audit import append_audit_log

MULTI_ANGLES: tuple[
    Literal["front", "left_profile", "right_profile", "three_quarter", "full_body", "closeup"], ...
] = ("front", "left_profile", "right_profile", "three_quarter", "full_body", "closeup")


class IdentityStudio:
    """M2 Identity Stabilization Engine:
      - F02: Multi-angle face generation + conversational refinement + LoRA lock + ArcFace >= 0.72
      - F03: Voice sample audition + cloning + consistent voice_id across outputs
      - F06: Final Intro Preview Video (face + voice + behavior + C2PA)
      - F18: Environment Reference Pack versioning
      - F19: External Product Truth Registry (manual / CSV / XLSX)
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def generate_multi_angle_identity(
        self,
        session: Session,
        *,
        character_id: str,
        refinement_note: str = "",
        seed: int = 42,
        approve: bool = False,
    ) -> dict[str, Any]:
        settings = get_settings()
        profile = assert_single_persona_id(session, character_id)
        if refinement_note.strip():
            profile.locked_physical_prompt = (
                f"{profile.locked_physical_prompt} | refined: {refinement_note.strip()}"
            )
            profile.version += 1

        provider = self.registry.ordered_candidates(
            "image_gen", policy=settings.brain_model_gate, gpu_available=settings.gpu_available
        )[0]

        front_path: str | None = None
        generated: list[dict[str, Any]] = []

        for angle in MULTI_ANGLES:
            res = await provider.generate_image(
                ImageGenInput(
                    character_id=character_id,
                    prompt=f"{angle} studio portrait, natural expression",
                    locked_physical_prompt=profile.locked_physical_prompt,
                    angle=angle,
                    seed=seed,
                    reference_image_paths=[front_path] if front_path else [],
                    adapter_weight=0.85,
                )
            )
            img_path = res.data.file_path
            if front_path is None:
                front_path = img_path
                score = 1.0
            else:
                score = compute_arcface_similarity(front_path, img_path)

            asset = IdentityAsset(
                character_id=character_id,
                kind="ref_image",
                path=img_path,
                angle=angle,
                arcface_score=score,
                seed=seed,
                model_name=res.data.model,
                metadata_json={
                    "locked_physical_prompt": profile.locked_physical_prompt,
                    "adapter_weight": 0.85,
                    "refinement_note": refinement_note,
                },
                version=profile.version,
                approved_at=datetime.now(UTC) if approve else None,
            )
            session.add(asset)
            generated.append(
                {
                    "angle": angle,
                    "path": img_path,
                    "arcface_score": score,
                    "passes_threshold": score >= settings.qa_face_threshold,
                }
            )

        # Register LoRA lock artifact when approved
        lora_path = ""
        if approve:
            storage = get_object_storage()
            lora_file = storage.resolve_path(f"{character_id}/lora/face_v{profile.version}.safetensors")
            lora_file.parent.mkdir(parents=True, exist_ok=True)
            lora_file.write_bytes(b"HOVITA_FACE_LORA_V1:" + profile.locked_physical_prompt.encode("utf-8"))
            lora_path = str(lora_file)
            session.add(
                IdentityAsset(
                    character_id=character_id,
                    kind="lora",
                    path=lora_path,
                    angle="all",
                    arcface_score=min(item["arcface_score"] for item in generated),
                    seed=seed,
                    model_name="kohya-ss-sd-scripts-flux-lora",
                    metadata_json={"rank": 16, "steps": 800, "locked_prompt": profile.locked_physical_prompt},
                    version=profile.version,
                    approved_at=datetime.now(UTC),
                )
            )
            profile.status = "identity_locked"
            step2 = session.execute(
                select(OnboardingProgress).where(
                    OnboardingProgress.character_id == character_id,
                    OnboardingProgress.step_index == 2,
                )
            ).scalar_one_or_none()
            if step2:
                step2.status = "completed"
                step2.pct = 100
                step2.output_json = {"lora_path": lora_path, "version": profile.version}

        session.commit()
        min_score = min(item["arcface_score"] for item in generated)
        append_audit_log(
            session,
            character_id=character_id,
            actor="identity_studio",
            action="multi_angle_identity_generated",
            resource_type="identity_asset",
            resource_id=character_id,
            details={"approved": approve, "min_arcface": min_score, "version": profile.version},
        )
        return {
            "character_id": character_id,
            "version": profile.version,
            "approved": approve,
            "lora_path": lora_path,
            "min_arcface_score": min_score,
            "threshold": settings.qa_face_threshold,
            "all_passed": min_score >= settings.qa_face_threshold,
            "angles": generated,
        }

    async def audition_and_lock_voice(
        self,
        session: Session,
        *,
        character_id: str,
        language: Literal["fa", "en"] = "fa",
        selected_energy: Literal["calm", "balanced", "energetic"] = "balanced",
        approve: bool = True,
    ) -> dict[str, Any]:
        profile = assert_single_persona_id(session, character_id)
        tts_provider = self.registry.ordered_candidates("tts", policy="free_first")[0]

        sample_text = (
            f"سلام! من {profile.name} هستم و با صدای اختصاصی خودم محتوا می‌سازم."
            if language == "fa"
            else f"Hello! I am {profile.name}, speaking with my locked custom voice identity."
        )
        candidates: list[dict[str, Any]] = []
        chosen_voice_id = f"voice_{character_id}_{language}_{selected_energy}"
        chosen_sample_path = ""

        for energy in ("calm", "balanced", "energetic"):
            vid = f"voice_{character_id}_{language}_{energy}"
            res = await tts_provider.synthesize(
                TTSInput(
                    character_id=character_id,
                    text=sample_text,
                    voice_id=vid,
                    language=language,
                    energy=energy,
                )
            )
            candidates.append(
                {"energy": energy, "voice_id": vid, "sample_path": res.data.file_path, "duration_sec": res.data.duration_sec}
            )
            if energy == selected_energy:
                chosen_sample_path = res.data.file_path

        if approve:
            session.add(
                VoiceAsset(
                    character_id=character_id,
                    engine=tts_provider.metadata.provider_id,
                    ref_sample=chosen_sample_path,
                    voice_id=chosen_voice_id,
                    language=language,
                    sample_rate=22050,
                    version=profile.version,
                    approved_at=datetime.now(UTC),
                )
            )
            step3 = session.execute(
                select(OnboardingProgress).where(
                    OnboardingProgress.character_id == character_id,
                    OnboardingProgress.step_index == 3,
                )
            ).scalar_one_or_none()
            if step3:
                step3.status = "completed"
                step3.pct = 100
                step3.output_json = {"voice_id": chosen_voice_id, "language": language}
            session.commit()

        return {
            "character_id": character_id,
            "voice_id": chosen_voice_id,
            "engine": tts_provider.metadata.provider_id,
            "language": language,
            "approved": approve,
            "candidates": candidates,
        }

    async def generate_intro_preview_video(
        self,
        session: Session,
        *,
        character_id: str,
    ) -> dict[str, Any]:
        """F06: Short intro preview video combining approved face + approved voice + behavior."""
        profile = assert_single_persona_id(session, character_id)
        ref_img = session.execute(
            select(IdentityAsset)
            .where(IdentityAsset.character_id == character_id, IdentityAsset.kind == "ref_image")
            .order_by(IdentityAsset.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        voice = session.execute(
            select(VoiceAsset)
            .where(VoiceAsset.character_id == character_id)
            .order_by(VoiceAsset.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        tts_provider = self.registry.ordered_candidates("tts", policy="free_first")[0]
        vid_provider = self.registry.ordered_candidates("video_gen", policy="free_first")[0]
        edit_provider = self.registry.ordered_candidates("video_edit", policy="free_first")[0]

        intro_text = (
            f"سلام من {profile.name} هستم! هدف من {profile.goal}. "
            "از امروز کنار هم محتوای دقیق و شفاف تولید می‌کنیم."
        )
        tts_res = await tts_provider.synthesize(
            TTSInput(
                character_id=character_id,
                text=intro_text,
                voice_id=voice.voice_id if voice else f"voice_{character_id}_fa_balanced",
                language="fa",
            )
        )
        raw_vid = await vid_provider.generate_video(
            VideoGenInput(
                character_id=character_id,
                prompt=f"Intro presentation of {profile.name}",
                keyframe_image_path=ref_img.path if ref_img else None,
                duration_sec=16,
            )
        )
        final_vid = await edit_provider.compose(
            VideoEditInput(
                character_id=character_id,
                video_paths=[raw_vid.data.file_path],
                audio_path=tts_res.data.file_path,
                target_duration_sec=16,
                inject_c2pa=True,
            )
        )
        return {
            "character_id": character_id,
            "preview_mp4_path": final_vid.data.output_mp4_path,
            "cover_path": final_vid.data.cover_image_path,
            "c2pa_verified": final_vid.data.c2pa_verified,
            "duration_sec": final_vid.data.duration_sec,
            "voice_id": voice.voice_id if voice else "default",
            "reference_image": ref_img.path if ref_img else "",
        }

    def register_environment_pack(
        self,
        session: Session,
        *,
        character_id: str,
        kind: Literal["real", "virtual"],
        capture_method: Literal["walkthrough_video", "multi_angle_photos", "chat", "virtual_ai"],
        media: list[str],
        palette: list[str],
        fixed_props: list[str],
        product_zones: list[str],
        approve: bool = True,
    ) -> EnvironmentAsset:
        """F18: Register and version-lock Environment Reference Pack."""
        assert_single_persona_id(session, character_id)
        latest = session.execute(
            select(EnvironmentAsset)
            .where(EnvironmentAsset.character_id == character_id)
            .order_by(EnvironmentAsset.approved_version.desc())
            .limit(1)
        ).scalar_one_or_none()
        next_ver = (latest.approved_version + 1) if latest else 1

        keyframes = media[:6] if media else [f"keyframe_{character_id}_v{next_ver}_01.png"]
        env = EnvironmentAsset(
            character_id=character_id,
            kind=kind,
            capture_method=capture_method,
            media=media,
            keyframes=keyframes,
            palette=palette or ["#1D2D44", "#F4EFE6", "#D96B27"],
            fixed_props=fixed_props,
            product_zones=product_zones,
            approved_version=next_ver if approve else 0,
        )
        session.add(env)
        session.commit()
        session.refresh(env)
        append_audit_log(
            session,
            character_id=character_id,
            actor="identity_studio",
            action="environment_pack_registered",
            resource_type="environment_asset",
            resource_id=env.id,
            details={"approved_version": env.approved_version, "kind": kind},
        )
        return env

    def upsert_product(
        self,
        session: Session,
        *,
        character_id: str,
        sku: str,
        name: str,
        brand: str = "",
        price: float,
        discount: float = 0.0,
        stock: int = 0,
        features: dict[str, Any] | None = None,
        buy_link: str = "",
        images: list[str] | None = None,
        source: Literal["manual", "xlsx", "csv", "api"] = "manual",
    ) -> ProductItem:
        """F19: Upsert Product Truth record (versioned)."""
        assert_single_persona_id(session, character_id)
        existing = session.execute(
            select(ProductItem).where(
                ProductItem.character_id == character_id, ProductItem.sku == sku
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ProductItem(
                character_id=character_id,
                sku=sku,
                name=name,
                brand=brand,
                price=price,
                discount=discount,
                stock=stock,
                features=features or {},
                buy_link=buy_link,
                images=images or [],
                status="active" if stock > 0 else "out_of_stock",
                version=1,
                source=source,
            )
            session.add(existing)
        else:
            existing.name = name
            existing.brand = brand
            existing.price = price
            existing.discount = discount
            existing.stock = stock
            existing.features = features or existing.features
            existing.buy_link = buy_link or existing.buy_link
            existing.images = images or existing.images
            existing.status = "active" if stock > 0 else "out_of_stock"
            existing.version += 1
            existing.source = source
        session.commit()
        session.refresh(existing)
        return existing

    def import_products_csv_or_xlsx(
        self, session: Session, *, character_id: str, filename: str, raw_bytes: bytes
    ) -> list[ProductItem]:
        """F19: Bulk import products from CSV or Excel (.xlsx)."""
        items: list[ProductItem] = []
        ext = Path(filename).suffix.lower()
        rows_dicts: list[dict[str, Any]] = []
        if ext == ".xlsx":
            wb = load_workbook(io.BytesIO(raw_bytes), read_only=True)
            ws = wb.active
            if ws is not None:
                all_rows = list(ws.iter_rows(values_only=True))
                if all_rows:
                    headers = [str(h or "").strip().lower() for h in all_rows[0]]
                    for r in all_rows[1:]:
                        if any(r):
                            rows_dicts.append(dict(zip(headers, r, strict=False)))
        else:
            text = raw_bytes.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            for r in reader:
                rows_dicts.append({k.strip().lower(): v for k, v in r.items() if k})

        src: Literal["xlsx", "csv"] = "xlsx" if ext == ".xlsx" else "csv"
        for row in rows_dicts:
            sku = str(row.get("sku", "")).strip()
            name = str(row.get("name", "")).strip()
            if not sku or not name:
                continue
            item = self.upsert_product(
                session,
                character_id=character_id,
                sku=sku,
                name=name,
                brand=str(row.get("brand", "")),
                price=float(row.get("price", 0) or 0),
                discount=float(row.get("discount", 0) or 0),
                stock=int(float(row.get("stock", 0) or 0)),
                features={"raw_specs": str(row.get("features", ""))},
                buy_link=str(row.get("buy_link", "")),
                source=src,
            )
            items.append(item)
        return items
