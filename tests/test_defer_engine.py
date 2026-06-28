"""
test_defer_engine.py
====================
Phase 1 — Unit tests for DeferEngine.

Tests APScheduler job lifecycle: schedule → fire → cancel.
No external dependencies — uses an in-process BackgroundScheduler with MemoryJobStore.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from scheduler.defer_engine import DeferEngine, JobRecord


# ── Helpers ───────────────────────────────────────────────────────────────────

def _noop_sync(pr_id, agents, meta=None):
    return {"status": "stub"}


async def _noop_async(pr_id, agents, meta=None):
    return {"status": "stub"}


# ── Immediate job scheduling ──────────────────────────────────────────────────

class TestScheduleImmediate:
    def setup_method(self):
        self.engine = DeferEngine(run_fn=_noop_sync)
        self.engine.start()

    def teardown_method(self):
        self.engine.stop(wait=False)

    def test_schedule_immediate_returns_job_id(self):
        job_id = self.engine.schedule_immediate("PR-1", ["api_agent"])
        assert job_id.startswith("imm_PR-1_")

    def test_immediate_job_appears_in_status(self):
        job_id = self.engine.schedule_immediate("PR-10", ["api_agent", "data_agent"])
        statuses = self.engine.status()
        ids = [s["job_id"] for s in statuses]
        assert job_id in ids

    def test_immediate_job_record_has_correct_type(self):
        job_id = self.engine.schedule_immediate("PR-2", ["api_agent"])
        record = self.engine._records[job_id]
        assert record.job_type == "immediate"
        assert record.pr_id == "PR-2"
        assert "api_agent" in record.agents

    def test_immediate_job_fires_within_5_seconds(self):
        fired = []

        def run_fn(pr_id, agents, meta=None):
            fired.append(pr_id)

        engine = DeferEngine(run_fn=run_fn)
        engine.start()
        engine.schedule_immediate("PR-3", ["api_agent"])
        time.sleep(3)
        engine.stop(wait=False)
        assert "PR-3" in fired, "Immediate job did not fire within 5 seconds"

    def test_async_run_fn_fires_correctly(self):
        fired = []

        async def async_fn(pr_id, agents, meta=None):
            fired.append(pr_id)

        engine = DeferEngine(run_fn=async_fn)
        engine.start()
        engine.schedule_immediate("PR-async", ["api_agent"])
        time.sleep(3)
        engine.stop(wait=False)
        assert "PR-async" in fired


# ── Deferred job scheduling ───────────────────────────────────────────────────

class TestScheduleDeferred:
    def setup_method(self):
        self.engine = DeferEngine(run_fn=_noop_sync)
        self.engine.start()

    def teardown_method(self):
        self.engine.stop(wait=False)

    def test_deferred_job_returns_job_id(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=2)
        job_id = self.engine.schedule_deferred("PR-5", ["perf_agent"], run_at=run_at)
        assert job_id.startswith("def_PR-5_")

    def test_deferred_job_record_has_correct_type(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=2)
        job_id = self.engine.schedule_deferred("PR-6", ["browser_agent"], run_at=run_at,
                                               carbon_g_saved=15.3)
        record = self.engine._records[job_id]
        assert record.job_type == "deferred"
        assert record.carbon_g_saved == pytest.approx(15.3)
        assert not record.fired

    def test_deferred_job_capped_at_timeout(self):
        """A window beyond DEFERRED_TIMEOUT_HOURS should be capped."""
        far_future = datetime.now(timezone.utc) + timedelta(hours=24)
        job_id = self.engine.schedule_deferred("PR-7", ["perf_agent"], run_at=far_future)
        record = self.engine._records[job_id]
        cap = datetime.now(timezone.utc) + timedelta(hours=8)
        assert record.run_at <= cap + timedelta(seconds=5)

    def test_deferred_job_none_run_at_uses_timeout(self):
        job_id = self.engine.schedule_deferred("PR-8", ["perf_agent"], run_at=None)
        record = self.engine._records[job_id]
        expected_fire = datetime.now(timezone.utc) + timedelta(hours=8)
        diff = abs((record.run_at - expected_fire).total_seconds())
        assert diff < 10, "No run_at should fire at ~DEFERRED_TIMEOUT_HOURS"

    def test_deferred_job_fires_when_window_arrives(self):
        fired = []

        def run_fn(pr_id, agents, meta=None):
            fired.append(pr_id)

        engine = DeferEngine(run_fn=run_fn)
        engine.start()
        run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        engine.schedule_deferred("PR-9", ["perf_agent"], run_at=run_at)
        time.sleep(4)
        engine.stop(wait=False)
        assert "PR-9" in fired

    def test_co2_saved_accumulated_after_fire(self):
        fired_event = []

        def run_fn(pr_id, agents, meta=None):
            fired_event.append(True)

        engine = DeferEngine(run_fn=run_fn)
        engine.start()
        run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        engine.schedule_deferred("PR-co2", ["perf_agent"], run_at=run_at, carbon_g_saved=25.0)
        time.sleep(4)
        engine.stop(wait=False)
        assert engine.total_co2_saved_g() == pytest.approx(25.0)


# ── Job cancellation ──────────────────────────────────────────────────────────

class TestCancelJobs:
    def setup_method(self):
        self.engine = DeferEngine(run_fn=_noop_sync)
        self.engine.start()

    def teardown_method(self):
        self.engine.stop(wait=False)

    def test_cancel_single_job(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=4)
        job_id = self.engine.schedule_deferred("PR-20", ["perf_agent"], run_at=run_at)
        result = self.engine.cancel(job_id)
        assert result is True
        assert self.engine._records[job_id].cancelled is True

    def test_cancel_nonexistent_job_returns_false(self):
        result = self.engine.cancel("def_INVALID_abc123")
        assert result is False

    def test_cancel_pr_removes_all_pending_jobs(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=3)
        j1 = self.engine.schedule_deferred("PR-30", ["perf_agent"], run_at=run_at)
        j2 = self.engine.schedule_deferred("PR-30", ["browser_agent"], run_at=run_at)
        count = self.engine.cancel_pr("PR-30")
        assert count == 2
        assert self.engine._records[j1].cancelled is True
        assert self.engine._records[j2].cancelled is True

    def test_cancel_pr_does_not_cancel_different_pr(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=3)
        j1 = self.engine.schedule_deferred("PR-40", ["perf_agent"], run_at=run_at)
        j2 = self.engine.schedule_deferred("PR-41", ["browser_agent"], run_at=run_at)
        self.engine.cancel_pr("PR-40")
        assert self.engine._records[j1].cancelled is True
        assert self.engine._records[j2].cancelled is False

    def test_cancelled_job_does_not_fire(self):
        fired = []

        def run_fn(pr_id, agents, meta=None):
            fired.append(pr_id)

        engine = DeferEngine(run_fn=run_fn)
        engine.start()
        run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        job_id = engine.schedule_deferred("PR-cancel", ["perf_agent"], run_at=run_at)
        engine.cancel(job_id)
        time.sleep(4)
        engine.stop(wait=False)
        assert "PR-cancel" not in fired


# ── status() and metrics ──────────────────────────────────────────────────────

class TestStatus:
    def setup_method(self):
        self.engine = DeferEngine(run_fn=_noop_sync)
        self.engine.start()

    def teardown_method(self):
        self.engine.stop(wait=False)

    def test_status_contains_all_scheduled_jobs(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=2)
        j1 = self.engine.schedule_immediate("PR-50", ["api_agent"])
        j2 = self.engine.schedule_deferred("PR-50", ["perf_agent"], run_at=run_at)
        ids = [s["job_id"] for s in self.engine.status()]
        assert j1 in ids
        assert j2 in ids

    def test_status_entry_has_required_keys(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=2)
        self.engine.schedule_deferred("PR-55", ["perf_agent"], run_at=run_at)
        entry = self.engine.status()[0]
        for key in ("job_id", "pr_id", "type", "agents", "run_at", "fired", "cancelled", "co2_saved_g"):
            assert key in entry

    def test_total_co2_saved_zero_before_fire(self):
        run_at = datetime.now(timezone.utc) + timedelta(hours=3)
        self.engine.schedule_deferred("PR-60", ["perf_agent"], run_at=run_at, carbon_g_saved=10.0)
        assert self.engine.total_co2_saved_g() == 0.0
