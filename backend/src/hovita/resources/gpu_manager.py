from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from hovita.config.settings import get_settings
from hovita.db.models import EmergencyState, GPUJobQueueItem
from hovita.security.audit import append_audit_log


class GPUStatus(BaseModel):
    mode: Literal["on_demand", "always_on", "hourly_rental"]
    state: Literal["sleeping", "waking", "awake"]
    gpu_available: bool
    idle_sleep_minutes: int
    last_active_at: datetime | None = None
    idle_seconds: float = 0.0
    queued_jobs: int = 0
    total_energy_kwh: float = 0.0
    total_gpu_minutes: float = 0.0
    cold_start_target_sec: int = 180


class GPUManager:
    """CPU always-on / GPU on-demand resource manager (Section 51).

    - Wakes GPU when queued work arrives (cold start < 3 min target).
    - Batches prioritized jobs (1=scheduled_today > 2=normal > 3=experiment).
    - Sleeps automatically after 10 minutes idle -> zero idle GPU energy cost.
    - When GPU unavailable/full, routes eligible jobs to economical API or holds queue.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.mode: Literal["on_demand", "always_on", "hourly_rental"] = settings.gpu_mode
        self.gpu_available: bool = settings.gpu_available
        self.state: Literal["sleeping", "waking", "awake"] = (
            "awake" if settings.gpu_mode == "always_on" and settings.gpu_available else "sleeping"
        )
        self.last_active_at: datetime | None = None
        self.total_energy_kwh: float = 0.0
        self.total_gpu_minutes: float = 0.0

    def wake(self, session: Session, *, character_id: str, reason: str = "job_arrived") -> GPUStatus:
        self.state = "awake"
        self.last_active_at = datetime.now(UTC)
        append_audit_log(
            session,
            character_id=character_id,
            actor="gpu_manager",
            action="gpu_wake",
            resource_type="gpu",
            resource_id="local_gpu",
            details={"reason": reason, "mode": self.mode},
        )
        return self.status(session)

    def sleep(self, session: Session, *, character_id: str, reason: str = "idle_timeout") -> GPUStatus:
        if self.mode == "always_on" and reason == "idle_timeout":
            return self.status(session)
        self.state = "sleeping"
        append_audit_log(
            session,
            character_id=character_id,
            actor="gpu_manager",
            action="gpu_sleep",
            resource_type="gpu",
            resource_id="local_gpu",
            details={"reason": reason, "mode": self.mode},
        )
        return self.status(session)

    def set_mode(
        self,
        session: Session,
        *,
        character_id: str,
        mode: Literal["on_demand", "always_on", "hourly_rental"],
    ) -> GPUStatus:
        self.mode = mode
        em = session.execute(select(EmergencyState)).scalar_one_or_none()
        if em:
            em.gpu_mode = mode
            session.commit()
        if mode == "always_on":
            return self.wake(session, character_id=character_id, reason="manual_always_on")
        return self.status(session)

    def check_idle_sleep(self, session: Session, *, character_id: str, now: datetime | None = None) -> bool:
        settings = get_settings()
        if self.mode == "always_on" or self.state == "sleeping" or self.last_active_at is None:
            return False
        ref_now = now or datetime.now(UTC)
        idle_limit = timedelta(minutes=settings.gpu_idle_sleep_minutes)
        if ref_now - self.last_active_at >= idle_limit:
            self.sleep(session, character_id=character_id, reason="idle_timeout")
            return True
        return False

    def enqueue_job(
        self,
        session: Session,
        *,
        character_id: str,
        task_type: str,
        priority: int = 2,
        payload: dict[str, object] | None = None,
    ) -> GPUJobQueueItem:
        item = GPUJobQueueItem(
            character_id=character_id,
            task_type=task_type,
            priority=priority,
            status="queued",
            payload_json=dict(payload or {}),
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    def pop_next_job(self, session: Session) -> GPUJobQueueItem | None:
        return session.execute(
            select(GPUJobQueueItem)
            .where(GPUJobQueueItem.status == "queued")
            .order_by(GPUJobQueueItem.priority.asc(), GPUJobQueueItem.created_at.asc())
            .limit(1)
        ).scalar_one_or_none()

    def dispatch_or_fallback(
        self,
        session: Session,
        *,
        character_id: str,
        task_type: str,
        priority: int = 2,
        gpu_minutes_est: float = 0.5,
    ) -> dict[str, object]:
        """Run on GPU if available (waking on demand & logging energy), else route to Eco API
        for urgent jobs or keep queued for the next GPU batch."""
        settings = get_settings()
        job = self.enqueue_job(session, character_id=character_id, task_type=task_type, priority=priority)
        if self.gpu_available:
            self.wake(session, character_id=character_id, reason=f"dispatch:{task_type}")
            energy_kwh = round((settings.gpu_power_watts / 1000.0) * (gpu_minutes_est / 60.0), 6)
            cost_usd = round(
                energy_kwh * settings.gpu_electricity_usd_per_kwh
                if self.mode != "hourly_rental"
                else (gpu_minutes_est / 60.0) * settings.gpu_rental_usd_per_hour,
                6,
            )
            self.total_energy_kwh = round(self.total_energy_kwh + energy_kwh, 6)
            self.total_gpu_minutes = round(self.total_gpu_minutes + gpu_minutes_est, 4)
            self.last_active_at = datetime.now(UTC)

            job.status = "completed"
            job.provider_used = "local_gpu"
            job.gpu_minutes = gpu_minutes_est
            job.energy_kwh = energy_kwh
            job.started_at = datetime.now(UTC)
            job.finished_at = datetime.now(UTC)
            session.commit()
            return {
                "job_id": job.id,
                "execution_target": "local_gpu",
                "gpu_minutes": gpu_minutes_est,
                "energy_kwh": energy_kwh,
                "energy_cost_usd": cost_usd,
            }

        # No GPU available: urgent scheduled work falls back to economical API; lower priority waits
        if priority == 1:
            job.status = "fallback_api"
            job.provider_used = "eco_api_fallback"
            job.finished_at = datetime.now(UTC)
            session.commit()
            return {
                "job_id": job.id,
                "execution_target": "eco_api_fallback",
                "gpu_minutes": 0.0,
                "energy_kwh": 0.0,
                "energy_cost_usd": 0.0,
            }
        return {
            "job_id": job.id,
            "execution_target": "queued_for_gpu",
            "gpu_minutes": 0.0,
            "energy_kwh": 0.0,
            "energy_cost_usd": 0.0,
        }

    def status(self, session: Session) -> GPUStatus:
        settings = get_settings()
        queued = list(
            session.execute(
                select(GPUJobQueueItem).where(GPUJobQueueItem.status == "queued")
            ).scalars()
        )
        idle_sec = 0.0
        if self.last_active_at and self.state == "awake":
            idle_sec = max(0.0, (datetime.now(UTC) - self.last_active_at).total_seconds())
        return GPUStatus(
            mode=self.mode,
            state=self.state,
            gpu_available=self.gpu_available,
            idle_sleep_minutes=settings.gpu_idle_sleep_minutes,
            last_active_at=self.last_active_at,
            idle_seconds=round(idle_sec, 1),
            queued_jobs=len(queued),
            total_energy_kwh=self.total_energy_kwh,
            total_gpu_minutes=self.total_gpu_minutes,
        )
