from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from hovita.db.models import ContentItem, EmergencyState, GPUJobQueueItem
from hovita.security.audit import append_audit_log


class EmergencyStoppedError(RuntimeError):
    """Raised when an operation is blocked due to active Emergency Kill Switch."""


def is_kill_switch_active(session: Session) -> bool:
    state = session.execute(select(EmergencyState)).scalar_one_or_none()
    return bool(state and state.kill_switch_active)


def assert_not_emergency_stopped(session: Session, operation: str = "operation") -> None:
    if is_kill_switch_active(session):
        raise EmergencyStoppedError(
            f"Operation '{operation}' blocked: Emergency Kill Switch is ACTIVE."
        )


def trigger_emergency_stop(
    session: Session,
    *,
    character_id: str,
    reason: str,
    actor: str = "human_manager",
) -> dict[str, int | str | bool]:
    """Activate emergency stop: flush scheduled/queued jobs, block publish & browser, audit."""
    state = session.execute(select(EmergencyState)).scalar_one_or_none()
    if state is None:
        state = EmergencyState(
            id="global",
            singleton_lock=1,
            character_id=character_id,
            kill_switch_active=True,
            reason=reason,
            triggered_by=actor,
            triggered_at=datetime.now(UTC),
            browser_enabled=False,
        )
        session.add(state)
    else:
        state.kill_switch_active = True
        state.reason = reason
        state.triggered_by = actor
        state.triggered_at = datetime.now(UTC)
        state.browser_enabled = False

    # Flush queued GPU jobs and scheduled content items back to draft/cancelled
    gpu_res = session.execute(
        update(GPUJobQueueItem)
        .where(GPUJobQueueItem.status == "queued")
        .values(status="cancelled")
    )
    sched_res = session.execute(
        update(ContentItem)
        .where(ContentItem.status == "scheduled")
        .values(status="awaiting_approval", publish_at=None)
    )
    session.commit()

    append_audit_log(
        session,
        character_id=character_id,
        actor=actor,
        action="emergency_kill_switch_activated",
        resource_type="emergency_state",
        resource_id="global",
        details={
            "reason": reason,
            "cancelled_gpu_jobs": int(gpu_res.rowcount or 0),  # type: ignore[attr-defined]
            "paused_scheduled_contents": int(sched_res.rowcount or 0),  # type: ignore[attr-defined]
        },
    )
    return {
        "kill_switch_active": True,
        "reason": reason,
        "cancelled_gpu_jobs": int(gpu_res.rowcount or 0),  # type: ignore[attr-defined]
        "paused_scheduled_contents": int(sched_res.rowcount or 0),  # type: ignore[attr-defined]
    }


def clear_emergency_stop(
    session: Session,
    *,
    character_id: str,
    actor: str = "human_manager",
    re_enable_browser: bool = True,
) -> bool:
    state = session.execute(select(EmergencyState)).scalar_one_or_none()
    if state is None:
        return False
    state.kill_switch_active = False
    state.reason = ""
    state.browser_enabled = re_enable_browser
    session.commit()
    append_audit_log(
        session,
        character_id=character_id,
        actor=actor,
        action="emergency_kill_switch_cleared",
        resource_type="emergency_state",
        resource_id="global",
        details={"re_enable_browser": re_enable_browser},
    )
    return True
