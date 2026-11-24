from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from hovita.config.settings import reset_settings_cache
from hovita.db.base import clear_db_caches, get_session_factory, init_db_schema
from hovita.db.repositories import seed_installation


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    db_file = tmp_path / "test_hovita.db"
    storage_dir = tmp_path / "storage"
    secret_file = tmp_path / "secrets.enc.json"

    monkeypatch.setenv("HOVITA_ENV", "test")
    monkeypatch.setenv("CHARACTER_ID", "persona-test-001")
    monkeypatch.setenv("CHARACTER_NAME", "Hovita Test Persona")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("REDIS_URL", "memory://")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("SECRET_STORE_PATH", str(secret_file))
    monkeypatch.setenv("SECRET_MASTER_KEY", "unit-test-master-key-do-not-use-in-prod")
    monkeypatch.setenv("DAILY_BUDGET_USD", "3.0")
    monkeypatch.setenv("BUDGET_WARN_PCT", "0.80")
    monkeypatch.setenv("QA_MAX_REVISIONS", "2")
    monkeypatch.setenv("QA_FACE_THRESHOLD", "0.72")
    monkeypatch.setenv("QA_HOOK_MIN_SCORE", "6.0")

    reset_settings_cache()
    clear_db_caches()
    init_db_schema(f"sqlite:///{db_file}")
    with get_session_factory(f"sqlite:///{db_file}")() as s:
        seed_installation(s)

    yield tmp_path

    clear_db_caches()
    reset_settings_cache()
    _ = os.environ.get("CHARACTER_ID")


@pytest.fixture()
def db_session(isolated_env: Path) -> Generator[Session, None, None]:
    _ = isolated_env
    factory = get_session_factory()
    with factory() as session:
        yield session
