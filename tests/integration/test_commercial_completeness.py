from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.agents.identity_studio import IdentityStudio
from hovita.api.app import app
from hovita.config.settings import reset_settings_cache
from hovita.contracts.base import ProviderError
from hovita.contracts.modules import WorkflowRunInput
from hovita.db.models import ApprovalRecord, ContentItem, MemoryEntry, SocialAccount
from hovita.modules.bootstrap import build_default_registry
from hovita.modules.workflow_engine.providers import N8nBridgeWorkflowProvider
from hovita.resources.browser_layer import BrowserActionRequest, BrowserLayer
from hovita.scheduler.runner import InternalScheduler

CID = "persona-test-001"


@pytest.mark.asyncio
async def test_scheduler_housekeeping_expiration_token_expiry_and_pause(
    db_session: Session,
) -> None:
    sched = InternalScheduler(build_default_registry())

    # 1. Expired approval > 24h gets cleanly expired
    c = ContentItem(character_id=CID, title="Stale Approval", status="awaiting_approval")
    db_session.add(c)
    db_session.flush()
    app_rec = ApprovalRecord(
        character_id=CID,
        content_id=c.id,
        decision="pending",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(app_rec)

    # 2. Social account token expiring in 12h triggers warning
    db_session.add(
        SocialAccount(
            character_id=CID,
            platform="instagram",
            handle="@expiring",
            token_expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
    )
    db_session.commit()

    res = sched.tick_housekeeping(db_session)
    assert res["expired_approvals"] == 1
    assert len(res["expiring_tokens"]) == 1
    db_session.refresh(c)
    assert c.status == "expired"

    # 3. G16 official metrics sync writes MetricSnapshot rows
    sync_res = await sched.sync_official_metrics(db_session, character_id=CID)
    assert sync_res["accounts_checked"] == 1
    assert sync_res["snapshots_recorded"] >= 1


@pytest.mark.asyncio
async def test_api_commercial_controls_import_ws_media_and_auth(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = build_default_registry()
    studio = IdentityStudio(reg)
    id_res = await studio.generate_multi_angle_identity(db_session, character_id=CID, approve=True)
    await studio.audition_and_lock_voice(db_session, character_id=CID, approve=True)
    front_img_path = id_res["angles"][0]["path"]

    with TestClient(app) as client:
        # 1. CSV multipart import
        csv_bytes = b"sku,name,brand,price,discount,stock,features,buy_link\nSKU-CSV-1,Rose Oil,Hovita,42.5,2.5,15,50ml,https://shop.local/rose\n"
        imp = client.post(
            "/api/v1/products/import",
            files={"file": ("catalog.csv", csv_bytes, "text/csv")},
        )
        assert imp.status_code == 200
        assert imp.json()["imported_count"] == 1

        # 2. Module hot-swap + reset
        sw = client.post(
            "/api/v1/modules/override",
            json={"module_name": "tts", "provider_id": "kokoro_82m_local"},
        )
        assert sw.status_code == 200
        assert sw.json()["active_override"] == "kokoro_82m_local"
        mods = client.get("/api/v1/modules").json()
        assert mods["active_overrides"]["tts"] == "kokoro_82m_local"
        client.post("/api/v1/modules/override", json={"module_name": "tts", "provider_id": ""})

        # 3. Pause & Resume persona
        assert client.post("/api/v1/system/pause").json()["status"] == "paused"
        assert client.post("/api/v1/system/resume").json()["status"] == "active"

        # 4. GPU Wake / Sleep / Mode
        g = client.post("/api/v1/system/gpu", json={"action": "set_mode", "mode": "always_on"})
        assert g.status_code == 200 and g.json()["mode"] == "always_on"
        client.post("/api/v1/system/gpu", json={"action": "sleep"})

        # 5. Media Endpoint: blocks path traversal, serves valid storage PNG
        bad_media = client.get("/api/v1/media", params={"path": "/etc/passwd"})
        assert bad_media.status_code == 403
        good_media = client.get("/api/v1/media", params={"path": front_img_path})
        assert good_media.status_code == 200
        assert good_media.headers["content-type"].startswith("image/")

        # 6. Inline edited_caption approval
        p_res = client.post(
            "/api/v1/pipeline/produce",
            json={"topic": "تست ویرایش کپشن", "mode": "new_idea", "platform": "sandbox"},
        )
        cid_item = p_res.json()["content_id"]
        dec = client.post(
            f"/api/v1/approvals/{cid_item}/decide",
            json={
                "decision": "approved",
                "edited_caption": "کپشن ویرایش‌شده توسط مدیر انسانی",
            },
        )
        assert dec.status_code == 200 and dec.json()["status"] == "published"

        # 7. Real-time WebSocket live call + memory summary persistence
        with client.websocket_connect("/ws/live-call/ws-test-01") as ws:
            ws.send_json({"utterance": "سلام در تماس زنده", "end_call": True})
            msg = ws.receive_json()
            assert msg["within_2s_sla"] is True
            assert msg["call_summary_memory_id"] is not None

        mem_row = db_session.execute(
            select(MemoryEntry).where(MemoryEntry.semantic_key == "live_call_ws-test-01")
        ).scalar_one_or_none()
        assert mem_row is not None

        # 8. Optional Admin Bearer token enforcement
        monkeypatch.setenv("ADMIN_API_KEY", "super-commercial-key")
        reset_settings_cache()
        assert client.get("/api/v1/system/status").status_code == 401
        assert (
            client.get(
                "/api/v1/system/status",
                headers={"Authorization": "Bearer super-commercial-key"},
            ).status_code
            == 200
        )
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        reset_settings_cache()


@pytest.mark.asyncio
async def test_n8n_conditional_gating_browser_png_and_clone_script(
    db_session: Session, tmp_path: Path
) -> None:
    # n8n disabled by default unless customer self-hosts / holds license
    n8n = N8nBridgeWorkflowProvider()
    with pytest.raises(ProviderError):
        await n8n.run_workflow(
            WorkflowRunInput(character_id=CID, pipeline_name="test_pipe", thread_id="t1")
        )

    # Read-only visual review produces real PNG image
    bl = BrowserLayer(profile_dir=tmp_path / "browser")
    res = bl.execute_action(
        db_session,
        character_id=CID,
        req=BrowserActionRequest(action="visual_review_screenshot", target_url="https://example.com"),
    )
    assert res.screenshot_path is not None and res.screenshot_path.endswith(".png")
    assert Path(res.screenshot_path).exists()

    # Clone install script produces isolated deployment with separate CHARACTER_ID & SECRET_MASTER_KEY
    clone_dir = tmp_path / "persona-clone-2"
    proc = subprocess.run(
        ["bash", "scripts/clone_install.sh", str(clone_dir), "persona-002", "Sara Clone"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "persona-002" in proc.stdout
    env_txt = (clone_dir / ".env").read_text(encoding="utf-8")
    assert "CHARACTER_ID=persona-002" in env_txt
    assert "CHARACTER_NAME=Sara Clone" in env_txt
