from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import text

from hovita.config.settings import get_settings
from hovita.db.base import get_engine, get_session_factory, init_db_schema
from hovita.db.repositories import seed_installation


def main() -> int:
    settings = get_settings()
    init_db_schema()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    with get_session_factory()() as session:
        profile = seed_installation(session)

    Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)
    try:
        import imageio_ffmpeg

        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_bin = "system-or-unavailable"

    report = {
        "status": "healthy",
        "character_id": profile.id,
        "character_name": profile.name,
        "database_dialect": engine.dialect.name,
        "storage_backend": settings.storage_backend,
        "ffmpeg_bin": ffmpeg_bin,
        "autonomy_level": profile.autonomy_level,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
