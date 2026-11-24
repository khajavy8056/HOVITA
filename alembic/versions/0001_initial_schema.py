"""Initial single-persona schema for HOVITA.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    from hovita.db import models  # noqa: F401
    from hovita.db.base import Base

    Base.metadata.create_all(bind=bind)

    # Ensure pgvector embedding index metadata when running on PostgreSQL
    if bind.dialect.name == "postgresql":
        op.execute(
            "COMMENT ON TABLE memory_entry IS 'Namespace-isolated memory store (character_id)'"
        )
    _ = sa.text("SELECT 1")


def downgrade() -> None:
    from hovita.db import models  # noqa: F401
    from hovita.db.base import Base

    Base.metadata.drop_all(bind=op.get_bind())
