from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from hovita.config.settings import get_settings


class Base(DeclarativeBase):
    pass


_ENGINE_CACHE: dict[str, Engine] = {}
_SESSION_FACTORY_CACHE: dict[str, sessionmaker[Session]] = {}


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    if url in _ENGINE_CACHE:
        return _ENGINE_CACHE[url]

    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # Ensure parent directory exists for SQLite file
        if "///" in url and ":memory:" not in url:
            db_path = Path(url.split("///", 1)[1])
            db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, connect_args=connect_args, future=True, pool_pre_ping=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    _ENGINE_CACHE[url] = engine
    return engine


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    url = database_url or get_settings().database_url
    if url not in _SESSION_FACTORY_CACHE:
        engine = get_engine(url)
        _SESSION_FACTORY_CACHE[url] = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
    return _SESSION_FACTORY_CACHE[url]


def get_db_session() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def init_db_schema(database_url: str | None = None) -> None:
    """Create schema tables if missing (idempotent)."""
    from hovita.db import models  # noqa: F401

    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)


def clear_db_caches() -> None:
    for eng in _ENGINE_CACHE.values():
        eng.dispose()
    _ENGINE_CACHE.clear()
    _SESSION_FACTORY_CACHE.clear()
