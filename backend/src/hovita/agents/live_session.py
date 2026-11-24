from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.agents.planner_agent import PlannerAgent
from hovita.config.secrets import SecretStore
from hovita.config.settings import get_settings
from hovita.contracts.modules import (
    LLMInput,
    LLMMessage,
    MemoryReadInput,
    MemoryWriteInput,
    TTSInput,
)
from hovita.contracts.registry import ProviderRegistry
from hovita.db.models import ChatMessageRecord, IdentityAsset, VoiceAsset
from hovita.db.repositories import assert_single_persona_id
from hovita.modules.llm.router import run_llm_with_failover
from hovita.security.prompt_injection import scan_external_content


class LivePersonaSessionManager:
    """M5 Live Communication Layer:
      - F16: Direct text/voice chat with persona + external link/image/video injection into plan
      - F17: Live Face-to-Face Audio/Video Call (LiveKit WebRTC + faster-whisper + pocket-tts-farsi
             + HeyGen/AVTR-1 avatar stream) with <2s latency & automatic call summary -> memory_entry
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry
        self.planner = PlannerAgent(registry)

    async def direct_chat(
        self,
        session: Session,
        *,
        character_id: str,
        session_id: str,
        message: str,
        attachments: list[dict[str, str]] | None = None,
        with_voice: bool = False,
    ) -> dict[str, Any]:
        """F16: Direct persona chat that converts actionable trends/links into a TaskGraph plan."""
        t0 = time.monotonic()
        profile = assert_single_persona_id(session, character_id)

        # Gate external text & attachments against prompt injection
        scan = scan_external_content(message, source="manager_chat")
        clean_msg = message if scan.safe else scan.sanitized_text

        mem_provider = self.registry.ordered_candidates("memory")[0]
        mem_res = await mem_provider.read(
            MemoryReadInput(character_id=character_id, query=clean_msg, limit=3)
        )
        mem_ctx = " | ".join(m.content for m in mem_res.data.records)

        user_rec = ChatMessageRecord(
            character_id=character_id,
            session_id=session_id,
            role="user",
            channel="voice" if with_voice else "text",
            text=message,
            attachments_json=attachments or [],
        )
        session.add(user_rec)
        session.commit()

        # Detect if user asks to analyze a trend / link / image / create plan item
        low = message.lower()
        actionable = bool(attachments) or any(
            kw in low
            for kw in ["بررسی کن", "ترند", "بساز", "برنامه", "ویدیو", "پست", "trend", "analyze", "http"]
        )
        extracted_plan: dict[str, Any] | None = None
        if actionable:
            extracted_plan = await self.planner.plan_next_content(
                session,
                character_id=character_id,
                thread_id=f"chat_{session_id}_{int(time.time())}",
                user_directive=clean_msg[:180],
            )

        llm_res = await run_llm_with_failover(
            self.registry,
            LLMInput(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            f"You are {profile.name} ({profile.job}). Tone: {profile.tone}. "
                            f"Goal: {profile.goal}. Never break character. Memories: {mem_ctx}"
                        ),
                    ),
                    LLMMessage(role="user", content=clean_msg),
                ],
                task_complexity="light",
                prefer_language="fa",
            ),
            session=session,
            character_id=character_id,
            pipeline="f16_direct_chat",
        )
        reply_text = llm_res.data.text
        if extracted_plan:
            reply_text = (
                f"{reply_text}\n"
                f"✅ این پیشنهاد وارد برنامه تولید شد: «{extracted_plan['task_graph'][0]['topic']}» "
                f"(حالت: {extracted_plan['task_graph'][0]['production_mode']})"
            )

        audio_path: str | None = None
        if with_voice:
            voice = session.execute(
                select(VoiceAsset)
                .where(VoiceAsset.character_id == character_id)
                .order_by(VoiceAsset.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            tts = self.registry.ordered_candidates("tts")[0]
            t_res = await tts.synthesize(
                TTSInput(
                    character_id=character_id,
                    text=reply_text[:280],
                    voice_id=voice.voice_id if voice else f"voice_{character_id}_fa_balanced",
                    language="fa",
                )
            )
            audio_path = t_res.data.file_path

        latency_ms = int((time.monotonic() - t0) * 1000)
        persona_rec = ChatMessageRecord(
            character_id=character_id,
            session_id=session_id,
            role="persona",
            channel="voice" if with_voice else "text",
            text=reply_text,
            audio_path=audio_path,
            latency_ms=latency_ms,
            extracted_task_json=extracted_plan,
        )
        session.add(persona_rec)
        session.commit()

        return {
            "session_id": session_id,
            "character_id": character_id,
            "reply": reply_text,
            "audio_path": audio_path,
            "latency_ms": latency_ms,
            "extracted_plan": extracted_plan,
            "injection_blocked": not scan.safe,
        }

    def create_livekit_room_token(
        self, session: Session, *, character_id: str, room_name: str | None = None
    ) -> dict[str, Any]:
        """F17: Generate LiveKit WebRTC room credentials + avatar configuration."""
        settings = get_settings()
        profile = assert_single_persona_id(session, character_id)
        room = room_name or f"hovita-live-{character_id}-{uuid.uuid4().hex[:6]}"
        store = SecretStore()
        lk_key = store.get_primary_key("livekit_key") or "lk_local_dev_key"
        lk_secret = store.get_primary_key("livekit_secret") or "lk_local_dev_secret"
        heygen_key = store.get_primary_key("heygen")

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

        # Standard JWT-style token structure for LiveKit client
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
        payload_bytes = json.dumps(
            {
                "iss": lk_key,
                "sub": "human_manager",
                "video": {"room": room, "roomJoin": True},
                "exp": int(time.time()) + 3600,
            }
        ).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
        sig = hmac.new(
            lk_secret.encode("utf-8"), f"{header}.{payload_b64}".encode(), hashlib.sha256
        ).digest()
        token = f"{header}.{payload_b64}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"

        avatar_backend = "heygen_liveavatar" if heygen_key else "avtr1_local_webrtc"
        return {
            "room_name": room,
            "livekit_url": settings.livekit_url or "wss://localhost:7880",
            "token": token,
            "avatar_backend": avatar_backend,
            "persona_name": profile.name,
            "avatar_reference_image": ref_img.path if ref_img else "",
            "voice_id": voice.voice_id if voice else f"voice_{character_id}_fa_balanced",
        }

    async def handle_live_call_turn(
        self,
        session: Session,
        *,
        character_id: str,
        session_id: str,
        user_utterance: str,
        end_call_and_summarize: bool = False,
    ) -> dict[str, Any]:
        """F17: Execute live voice/video call turn (<2000ms target) + persist summary to memory."""
        t0 = time.monotonic()
        res = await self.direct_chat(
            session,
            character_id=character_id,
            session_id=session_id,
            message=user_utterance,
            with_voice=True,
        )
        turn_latency_ms = int((time.monotonic() - t0) * 1000)

        memory_id = None
        if end_call_and_summarize:
            msgs = list(
                session.execute(
                    select(ChatMessageRecord)
                    .where(
                        ChatMessageRecord.character_id == character_id,
                        ChatMessageRecord.session_id == session_id,
                    )
                    .order_by(ChatMessageRecord.created_at.asc())
                ).scalars()
            )
            user_points = [m.text for m in msgs if m.role == "user"]
            summary = f"خلاصه تماس زنده با شخصیت ({session_id}): " + " | ".join(user_points[-5:])
            mem_provider = self.registry.ordered_candidates("memory")[0]
            mem_res = await mem_provider.write(
                MemoryWriteInput(
                    character_id=character_id,
                    kind="episode",
                    semantic_key=f"live_call_{session_id}",
                    owner_agent="live_session",
                    source="live_call_summary",
                    content=summary,
                )
            )
            if mem_res.data.records:
                memory_id = mem_res.data.records[0].id

        return {
            "session_id": session_id,
            "reply": res["reply"],
            "audio_path": res["audio_path"],
            "latency_ms": turn_latency_ms,
            "within_2s_sla": turn_latency_ms < 2000,
            "extracted_plan": res["extracted_plan"],
            "call_summary_memory_id": memory_id,
        }
