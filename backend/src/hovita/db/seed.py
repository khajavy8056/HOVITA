from __future__ import annotations

from hovita.db.base import get_session_factory, init_db_schema
from hovita.db.repositories import seed_installation


def run_seed(database_url: str | None = None) -> str:
    init_db_schema(database_url)
    factory = get_session_factory(database_url)
    with factory() as session:
        profile = seed_installation(session)
        return profile.id


if __name__ == "__main__":
    cid = run_seed()
    print(f"Seeded single-persona installation: {cid}")
