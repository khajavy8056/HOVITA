from __future__ import annotations

from fastapi.testclient import TestClient
from scripts.healthcheck import main as healthcheck_main

from hovita.api.app import app


def test_smoke_healthcheck_and_core_routes() -> None:
    rc = healthcheck_main()
    assert rc == 0

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        status_resp = client.get("/api/v1/system/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["audit_chain_valid"] is True
        assert client.get("/api/v1/persona").status_code == 200
        assert client.get("/api/v1/onboarding/steps").status_code == 200
        assert client.get("/api/v1/costs/summary").status_code == 200
