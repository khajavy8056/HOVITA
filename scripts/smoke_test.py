from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient


def main() -> int:
    os.environ.setdefault("HOVITA_ENV", "test")
    os.environ.setdefault("DATABASE_URL", "sqlite:///data/smoke_hovita.db")
    os.environ.setdefault("LOCAL_STORAGE_DIR", "data/smoke_storage")

    from hovita.api.main import app
    from scripts.healthcheck import main as healthcheck_main

    if healthcheck_main() != 0:
        return 1

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/healthz").status_code == 200
        st = client.get("/api/v1/system/status")
        assert st.status_code == 200
        assert st.json()["audit_chain_valid"] is True

        mods = client.get("/api/v1/modules")
        assert mods.status_code == 200
        assert len(mods.json()) == 12

        builder = client.post(
            "/api/v1/persona/builder",
            json={
                "thread_id": "smoke-t1",
                "message": "شخصیت آزمایشی اسموک اینفلوئنسر اینستاگرام بساز",
                "explicit_fields": {
                    "name": "آوا اسموک",
                    "appearance_desc": "۲۵ ساله، استایل ساده",
                    "tone_desc": "صمیمی و دقیق",
                    "platforms": ["instagram_reels"],
                },
            },
        )
        assert builder.status_code == 200
        assert builder.json()["completed"] is True

        plan = client.post(
            "/api/v1/planner/plan",
            json={"preferred_platform": "sandbox", "user_directive": "معرفی کوتاه محصول"},
        )
        assert plan.status_code == 200

    # Clean temporary smoke artifacts if desired? Keep smoke DB small or inside data/
    Path("data").mkdir(exist_ok=True)
    print(json.dumps({"smoke_test": "passed", "modules": 12}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
