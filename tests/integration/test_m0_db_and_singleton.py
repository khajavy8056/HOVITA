from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hovita.db.models import CharacterProfile
from hovita.db.repositories import (
    SinglePersonaViolationError,
    assert_single_persona_id,
    seed_installation,
)


def test_seed_idempotent_and_single_persona_db_enforced(db_session: Session) -> None:
    # Seed again -> idempotent, returns same single persona
    p1 = seed_installation(db_session)
    p2 = seed_installation(db_session)
    assert p1.id == p2.id == "persona-test-001"

    # Attempting to insert a second persona in the same DB MUST fail at DB constraint level
    second = CharacterProfile(id="persona-illegal-002", singleton_lock=1, name="Second Persona")
    db_session.add(second)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Repository guard also rejects mismatched character_id
    with pytest.raises(SinglePersonaViolationError):
        assert_single_persona_id(db_session, "persona-illegal-002")
