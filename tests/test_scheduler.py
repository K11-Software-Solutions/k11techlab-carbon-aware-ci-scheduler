"""
test_scheduler.py
=================
Phase 1 — Unit tests for CarbonAwareScheduler.

Uses mock CarbonAwareClient and a no-op run_fn to test scheduler logic
without any external services.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheduler.carbon_client import CarbonForecast, CarbonWindow
from scheduler.risk_router import RiskBucket
from scheduler.scheduler import CarbonAwareScheduler, PREvent, SchedulerMetrics


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_forecast(intensity: float, threshold: float = 400.0) -> CarbonForecast:
    now = datetime.now(timezone.utc)
    optimal = CarbonWindow(
        zone="IE", start=now + timedelta(hours=2), end=now + timedelta(hours=2, minutes=30),
        intensity=80.0, is_optimal=True,
    )
    return CarbonForecast(
        zone="IE",
        current_intensity=intensity,
        windows=[optimal],
        optimal_window=optimal,
        is_high_carbon_now=(intensity >= threshold),
    )


async def _noop_run(pr_id, agents, meta=None):
    return {"status": "stub", "verdict": "PASS"}


@pytest.fixture
def mock_client_low():
    client = AsyncMock()
    client.get_forecast = AsyncMock(return_value=_make_forecast(200.0))
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_client_high():
    client = AsyncMock()
    client.get_forecast = AsyncMock(return_value=_make_forecast(550.0))
    client.close = AsyncMock()
    return client


# ── submit() routing outcomes ─────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSchedulerSubmit:
    async def test_submit_critical_pr_no_deferral(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        pr = PREvent(pr_id="PR-crit", risk_score=0.95, zone="IE")
        decision = await sched.submit(pr)
        sched.stop()
        assert decision.risk_bucket == RiskBucket.CRITICAL
        assert not decision.has_deferred_jobs

    async def test_submit_low_risk_high_carbon_has_deferred(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        pr = PREvent(pr_id="PR-low", risk_score=0.25, zone="IE")
        decision = await sched.submit(pr)
        sched.stop()
        assert decision.risk_bucket == RiskBucket.LOW
        assert decision.has_deferred_jobs

    async def test_submit_low_risk_low_carbon_no_deferral(self, mock_client_low):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_low,
            run_fn=_noop_run,
        )
        sched.start()
        pr = PREvent(pr_id="PR-lowc", risk_score=0.25)
        decision = await sched.submit(pr)
        sched.stop()
        assert not decision.has_deferred_jobs

    async def test_submit_force_full_bypasses_carbon(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        pr = PREvent(pr_id="PR-force", risk_score=0.20, force_full=True)
        decision = await sched.submit(pr)
        sched.stop()
        assert not decision.has_deferred_jobs
        assert "perf_agent" in decision.immediate_agents

    async def test_submit_with_sla_deadline(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        deadline = datetime.now(timezone.utc) + timedelta(hours=1)
        pr = PREvent(pr_id="PR-sla", risk_score=0.80, sla_deadline=deadline)
        decision = await sched.submit(pr)
        sched.stop()
        if decision.deferred_window:
            assert decision.deferred_window.start < deadline

    async def test_submit_returns_routing_decision(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        pr = PREvent(pr_id="PR-ret", risk_score=0.65)
        decision = await sched.submit(pr)
        sched.stop()
        assert decision.pr_id == "PR-ret"
        assert decision.carbon_intensity == pytest.approx(550.0, rel=0.01)


# ── metrics() ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSchedulerMetrics:
    async def test_metrics_increments_on_submit(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        await sched.submit(PREvent(pr_id="PR-m1", risk_score=0.50))
        await sched.submit(PREvent(pr_id="PR-m2", risk_score=0.50))
        m = sched.metrics()
        sched.stop()
        assert m["carbon_scheduler_prs_submitted_total"] == 2

    async def test_metrics_critical_counter(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        await sched.submit(PREvent(pr_id="PR-crit1", risk_score=0.95))
        await sched.submit(PREvent(pr_id="PR-crit2", risk_score=0.92))
        await sched.submit(PREvent(pr_id="PR-low1", risk_score=0.20))
        m = sched.metrics()
        sched.stop()
        assert m["carbon_scheduler_prs_submitted_total"] == 3

    async def test_metrics_has_expected_keys(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        await sched.submit(PREvent(pr_id="PR-keys", risk_score=0.50))
        m = sched.metrics()
        sched.stop()
        expected_keys = {
            "carbon_scheduler_prs_submitted_total",
            "carbon_scheduler_immediate_jobs_total",
            "carbon_scheduler_deferred_jobs_total",
            "carbon_scheduler_co2_saved_grams_total",
            "carbon_scheduler_jobs_pending",
        }
        assert expected_keys.issubset(set(m.keys()))


# ── cancel_pr() ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCancelPr:
    async def test_cancel_pr_clears_deferred_jobs(self, mock_client_high):
        sched = CarbonAwareScheduler(
            carbon_client=mock_client_high,
            run_fn=_noop_run,
        )
        sched.start()
        pr = PREvent(pr_id="PR-cancel", risk_score=0.30)
        decision = await sched.submit(pr)
        if decision.has_deferred_jobs:
            cancelled = sched.cancel_pr("PR-cancel")
            assert cancelled >= 1
        sched.stop()


# ── context manager ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_context_manager():
    client = AsyncMock()
    client.get_forecast = AsyncMock(return_value=_make_forecast(200.0))
    client.close = AsyncMock()

    async with CarbonAwareScheduler(carbon_client=client, run_fn=_noop_run) as sched:
        decision = await sched.submit(PREvent(pr_id="PR-ctx", risk_score=0.30))
        assert decision is not None

    client.close.assert_called_once()
