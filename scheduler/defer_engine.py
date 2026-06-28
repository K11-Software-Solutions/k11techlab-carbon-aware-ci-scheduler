"""
defer_engine.py
===============
APScheduler-based deferral engine for the carbon-aware CI scheduler.

Deferred jobs are stored in an APScheduler job store and fired when their
scheduled start time arrives. The engine is responsible for:

  1. Registering an immediate job (runs now, in the background).
  2. Registering a deferred job (fires at the optimal low-carbon window start).
  3. Cancelling deferred jobs when a PR is closed or superseded.
  4. Emitting structured log events that can be shipped to any observability sink.

Architecture
------------
                   ┌─────────────────────────────────┐
  scheduler.py ──► │         DeferEngine              │
                   │  APScheduler (BackgroundScheduler)│
                   │  ┌──────────┐  ┌──────────────┐  │
                   │  │ Immediate│  │  Deferred    │  │
                   │  │  Jobs    │  │  Jobs        │  │
                   │  └──────────┘  └──────────────┘  │
                   └─────────────────────────────────┘
                                │
                    qa_pipeline_adapter.run_agents()

Job IDs
-------
  immediate: "imm_{pr_id}_{timestamp}"
  deferred:  "def_{pr_id}_{timestamp}"

This ensures a PR can have at most one of each type active, and old jobs
are replaced when a new commit to the same PR arrives.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

# How long to wait before a deferred job auto-runs even if the carbon window
# never improves (safety net so jobs never get lost indefinitely).
DEFERRED_TIMEOUT_HOURS = int(os.getenv("DEFERRED_TIMEOUT_HOURS", "8"))


@dataclass
class JobRecord:
    job_id:      str
    pr_id:       str
    job_type:    str   # "immediate" | "deferred"
    agents:      list[str]
    run_at:      datetime
    fired:       bool = False
    cancelled:   bool = False
    fired_at:    Optional[datetime] = None
    carbon_g_saved: float = 0.0


class DeferEngine:
    """
    Manages immediate and deferred CI job execution via APScheduler.

    Usage:
        engine = DeferEngine(run_fn=qa_adapter.run_agents)
        engine.start()

        # From scheduler.py:
        engine.schedule_immediate(pr_id="PR-42", agents=[...])
        engine.schedule_deferred(pr_id="PR-42", agents=[...], run_at=window.start,
                                 carbon_g_saved=12.4)

        # On shutdown:
        engine.stop()
    """

    def __init__(self, run_fn: Callable) -> None:
        """
        Args:
            run_fn: async or sync callable with signature
                    run_fn(pr_id: str, agents: list[str]) -> dict
                    This is typically qa_pipeline_adapter.run_agents().
        """
        self._run_fn   = run_fn
        self._records: dict[str, JobRecord] = {}

        self._scheduler = BackgroundScheduler(
            jobstores={"default": MemoryJobStore()},
            executors={"default": ThreadPoolExecutor(max_workers=4)},
            job_defaults={"coalesce": True, "max_instances": 1},
            timezone="UTC",
        )

    def start(self) -> None:
        self._scheduler.start()
        logger.info("DeferEngine started (APScheduler BackgroundScheduler)")

    def stop(self, wait: bool = True) -> None:
        self._scheduler.shutdown(wait=wait)
        logger.info("DeferEngine stopped")

    # ── Public scheduling API ─────────────────────────────────────────────────

    def schedule_immediate(
        self,
        pr_id:  str,
        agents: list[str],
        meta:   Optional[dict] = None,
    ) -> str:
        """Schedule a job to run as soon as possible (within seconds)."""
        run_at  = datetime.now(timezone.utc) + timedelta(seconds=2)
        job_id  = f"imm_{pr_id}_{_short_id()}"
        record  = JobRecord(
            job_id=job_id, pr_id=pr_id,
            job_type="immediate", agents=agents, run_at=run_at,
        )
        self._records[job_id] = record
        self._scheduler.add_job(
            func=self._fire,
            trigger=DateTrigger(run_date=run_at),
            id=job_id,
            name=f"imm:{pr_id}",
            kwargs={"job_id": job_id, "meta": meta or {}},
            replace_existing=True,
        )
        logger.info("Scheduled IMMEDIATE job %s for PR %s — agents: %s",
                    job_id, pr_id, agents)
        return job_id

    def schedule_deferred(
        self,
        pr_id:          str,
        agents:         list[str],
        run_at:         Optional[datetime] = None,
        carbon_g_saved: float = 0.0,
        meta:           Optional[dict] = None,
    ) -> str:
        """
        Schedule a job to run at a specific future time (the low-carbon window).
        If run_at is None, falls back to DEFERRED_TIMEOUT_HOURS from now.
        """
        fallback = datetime.now(timezone.utc) + timedelta(hours=DEFERRED_TIMEOUT_HOURS)
        fire_at  = run_at if run_at else fallback

        # Safety net: never defer beyond the timeout
        deadline = datetime.now(timezone.utc) + timedelta(hours=DEFERRED_TIMEOUT_HOURS)
        if fire_at > deadline:
            logger.warning("Deferred window %s exceeds timeout; capping at %s", fire_at, deadline)
            fire_at = deadline

        job_id = f"def_{pr_id}_{_short_id()}"
        record = JobRecord(
            job_id=job_id, pr_id=pr_id,
            job_type="deferred", agents=agents, run_at=fire_at,
            carbon_g_saved=carbon_g_saved,
        )
        self._records[job_id] = record

        self._scheduler.add_job(
            func=self._fire,
            trigger=DateTrigger(run_date=fire_at),
            id=job_id,
            name=f"def:{pr_id}",
            kwargs={"job_id": job_id, "meta": meta or {}},
            replace_existing=True,
        )
        logger.info(
            "Scheduled DEFERRED job %s for PR %s at %s UTC — agents: %s  CO2 saved: %.2fg",
            job_id, pr_id, fire_at.strftime("%H:%M"), agents, carbon_g_saved,
        )
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel a pending job. Returns True if the job was found and removed."""
        try:
            self._scheduler.remove_job(job_id)
            if job_id in self._records:
                self._records[job_id].cancelled = True
            logger.info("Cancelled job %s", job_id)
            return True
        except Exception:
            logger.debug("Job %s not found for cancellation (may have already fired)", job_id)
            return False

    def cancel_pr(self, pr_id: str) -> int:
        """Cancel all pending jobs for a PR (e.g., on PR close). Returns count cancelled."""
        cancelled = 0
        for job_id, record in list(self._records.items()):
            if record.pr_id == pr_id and not record.fired and not record.cancelled:
                if self.cancel(job_id):
                    cancelled += 1
        logger.info("Cancelled %d job(s) for PR %s", cancelled, pr_id)
        return cancelled

    def status(self) -> list[dict]:
        """Return a snapshot of all job records for observability."""
        return [
            {
                "job_id":        r.job_id,
                "pr_id":         r.pr_id,
                "type":          r.job_type,
                "agents":        r.agents,
                "run_at":        r.run_at.isoformat(),
                "fired":         r.fired,
                "fired_at":      r.fired_at.isoformat() if r.fired_at else None,
                "cancelled":     r.cancelled,
                "co2_saved_g":   r.carbon_g_saved,
            }
            for r in self._records.values()
        ]

    def total_co2_saved_g(self) -> float:
        """Total estimated CO2 saved across all deferred jobs fired so far."""
        return sum(
            r.carbon_g_saved for r in self._records.values()
            if r.fired and r.job_type == "deferred"
        )

    # ── Internal job runner ───────────────────────────────────────────────────

    def _fire(self, job_id: str, meta: dict) -> None:
        """Called by APScheduler when a job's trigger fires."""
        record = self._records.get(job_id)
        if not record:
            logger.error("Job %s fired but has no record — skipping", job_id)
            return
        if record.cancelled:
            logger.info("Job %s was cancelled before firing — skipping", job_id)
            return

        record.fired    = True
        record.fired_at = datetime.now(timezone.utc)

        logger.info(
            "Firing %s job %s for PR %s (agents: %s)",
            record.job_type, job_id, record.pr_id, record.agents,
        )

        try:
            # Support both async and sync run functions
            if asyncio.iscoroutinefunction(self._run_fn):
                # Run in a new event loop since APScheduler uses threads
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(
                    self._run_fn(pr_id=record.pr_id, agents=record.agents, meta=meta)
                )
                loop.close()
            else:
                result = self._run_fn(pr_id=record.pr_id, agents=record.agents, meta=meta)

            logger.info("Job %s completed: %s", job_id, result)

        except Exception as exc:
            logger.exception("Job %s failed: %s", job_id, exc)


def _short_id() -> str:
    return uuid.uuid4().hex[:8]
