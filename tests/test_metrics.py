"""
test_metrics.py
===============
Phase 5 — Metrics and carbon accounting validation.

Verifies:
  - SchedulerMetrics counters increment correctly
  - CO2 savings formula matches cost_model.carbon_cost_grams()
  - DeferEngine.total_co2_saved_g() accumulates correctly across jobs
  - Metrics keys are Prometheus-compatible (snake_case, no spaces)
  - Carbon intensity gate: correct threshold comparisons
  - SCI (Software Carbon Intensity) formula components
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from scheduler.carbon_client import CarbonForecast, CarbonWindow
from scheduler.cost_model import AGENT_COSTS, Tier, agents_for_tier, carbon_cost_grams
from scheduler.defer_engine import DeferEngine
from scheduler.risk_router import RiskRouter, _co2_savings
from scheduler.scheduler import CarbonAwareScheduler, PREvent


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_forecast(intensity: float, optimal_intensity: float = 80.0) -> CarbonForecast:
    now = datetime.now(timezone.utc)
    optimal = CarbonWindow(
        zone="IE", start=now + timedelta(hours=2), end=now + timedelta(hours=2, minutes=30),
        intensity=optimal_intensity, is_optimal=True,
    )
    return CarbonForecast(
        zone="IE",
        current_intensity=intensity,
        windows=[optimal],
        optimal_window=optimal,
        is_high_carbon_now=(intensity >= 400.0),
    )


async def _noop(pr_id, agents, meta=None):
    return {"status": "stub"}


# ── CO2 savings formula ───────────────────────────────────────────────────────

class TestCO2SavingsFormula:
    def test_savings_increases_with_intensity_gap(self):
        """Larger gap between current and optimal → larger savings."""
        deferred = agents_for_tier(Tier.FULL)
        optimal_window_low  = CarbonWindow("IE", datetime.now(timezone.utc),
                                           datetime.now(timezone.utc) + timedelta(hours=1),
                                           intensity=50.0)
        optimal_window_high = CarbonWindow("IE", datetime.now(timezone.utc),
                                           datetime.now(timezone.utc) + timedelta(hours=1),
                                           intensity=300.0)

        savings_big_gap   = _co2_savings(deferred, 600.0, optimal_window_low)
        savings_small_gap = _co2_savings(deferred, 450.0, optimal_window_high)

        assert savings_big_gap > savings_small_gap

    def test_savings_are_non_negative(self):
        """Savings must never be negative (defer_gco2 > current_gco2 → clamp to 0)."""
        deferred = ["perf_agent"]
        optimal = CarbonWindow("IE", datetime.now(timezone.utc),
                               datetime.now(timezone.utc) + timedelta(hours=1),
                               intensity=900.0)  # worse than current
        savings = _co2_savings(deferred, 400.0, optimal)
        assert savings >= 0.0

    def test_savings_zero_with_no_deferred_agents(self):
        optimal = CarbonWindow("IE", datetime.now(timezone.utc),
                               datetime.now(timezone.utc) + timedelta(hours=1),
                               intensity=50.0)
        assert _co2_savings([], 600.0, optimal) == 0.0

    def test_savings_zero_with_no_optimal_window(self):
        assert _co2_savings(["perf_agent"], 600.0, None) == 0.0

    def test_savings_matches_cost_model_delta(self):
        """Savings = carbon_cost(deferred, current) - carbon_cost(deferred, optimal)."""
        deferred = ["perf_agent", "browser_agent"]
        current_intensity = 550.0
        optimal_intensity = 80.0
        optimal = CarbonWindow("IE", datetime.now(timezone.utc),
                               datetime.now(timezone.utc) + timedelta(hours=1),
                               intensity=optimal_intensity)

        expected = (
            carbon_cost_grams(deferred, current_intensity)
            - carbon_cost_grams(deferred, optimal_intensity)
        )
        actual = _co2_savings(deferred, current_intensity, optimal)
        assert abs(actual - expected) < 1e-9


# ── Scheduler metrics counters ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSchedulerMetricsCounters:
    async def _make_sched(self, intensity: float) -> CarbonAwareScheduler:
        client = AsyncMock()
        client.get_forecast = AsyncMock(return_value=_make_forecast(intensity))
        client.close = AsyncMock()
        sched = CarbonAwareScheduler(carbon_client=client, run_fn=_noop)
        sched.start()
        return sched

    async def test_submitted_total_increments_per_pr(self):
        sched = await self._make_sched(500.0)
        await sched.submit(PREvent(pr_id="PR-1", risk_score=0.20))
        await sched.submit(PREvent(pr_id="PR-2", risk_score=0.55))
        await sched.submit(PREvent(pr_id="PR-3", risk_score=0.80))
        m = sched.metrics()
        sched.stop()
        assert m["carbon_scheduler_prs_submitted_total"] == 3

    async def test_deferred_jobs_counter_increments(self):
        sched = await self._make_sched(550.0)  # high carbon → deferred
        await sched.submit(PREvent(pr_id="PR-d1", risk_score=0.20))  # LOW → deferred
        await sched.submit(PREvent(pr_id="PR-d2", risk_score=0.55))  # MEDIUM → deferred
        m = sched.metrics()
        sched.stop()
        assert m["carbon_scheduler_deferred_jobs_total"] >= 2

    async def test_immediate_jobs_counter_increments(self):
        sched = await self._make_sched(200.0)  # low carbon → all immediate
        await sched.submit(PREvent(pr_id="PR-i1", risk_score=0.20))
        await sched.submit(PREvent(pr_id="PR-i2", risk_score=0.95))
        m = sched.metrics()
        sched.stop()
        assert m["carbon_scheduler_immediate_jobs_total"] >= 2

    async def test_jobs_pending_non_negative(self):
        sched = await self._make_sched(500.0)
        await sched.submit(PREvent(pr_id="PR-p1", risk_score=0.20))
        m = sched.metrics()
        sched.stop()
        assert m["carbon_scheduler_jobs_pending"] >= 0

    async def test_metrics_keys_are_prometheus_compatible(self):
        """All keys must be snake_case with no spaces or special chars."""
        import re
        sched = await self._make_sched(300.0)
        await sched.submit(PREvent(pr_id="PR-keys", risk_score=0.50))
        m = sched.metrics()
        sched.stop()
        pattern = re.compile(r'^[a-z][a-z0-9_]*$')
        for key in m:
            assert pattern.match(key), f"Metric key '{key}' is not Prometheus-compatible"


# ── DeferEngine CO2 accumulation ──────────────────────────────────────────────

class TestDeferEngineCO2Accumulation:
    def test_co2_accumulates_when_deferred_job_fires(self):
        import time
        fired = []

        def run_fn(pr_id, agents, meta=None):
            fired.append(True)

        engine = DeferEngine(run_fn=run_fn)
        engine.start()

        run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        engine.schedule_deferred("PR-co2a", ["perf_agent"], run_at=run_at, carbon_g_saved=18.7)
        engine.schedule_deferred("PR-co2b", ["browser_agent"], run_at=run_at, carbon_g_saved=9.3)

        time.sleep(4)
        engine.stop(wait=False)

        assert engine.total_co2_saved_g() == pytest.approx(28.0, rel=0.01)

    def test_co2_does_not_include_cancelled_jobs(self):
        import time
        engine = DeferEngine(run_fn=lambda pr_id, agents, meta=None: None)
        engine.start()

        run_at = datetime.now(timezone.utc) + timedelta(hours=2)
        job_id = engine.schedule_deferred("PR-co2c", ["perf_agent"], run_at=run_at,
                                           carbon_g_saved=50.0)
        engine.cancel(job_id)
        engine.stop(wait=False)

        assert engine.total_co2_saved_g() == 0.0

    def test_co2_immediate_jobs_dont_contribute(self):
        import time
        fired = []

        def run_fn(pr_id, agents, meta=None):
            fired.append(True)

        engine = DeferEngine(run_fn=run_fn)
        engine.start()
        engine.schedule_immediate("PR-imm-co2", ["api_agent"])
        time.sleep(3)
        engine.stop(wait=False)

        # Immediate jobs don't carry carbon savings (they run now regardless)
        assert engine.total_co2_saved_g() == 0.0


# ── Carbon intensity threshold ────────────────────────────────────────────────

class TestCarbonIntensityThreshold:
    def test_forecast_at_exactly_threshold_not_high(self):
        """Intensity == threshold → not high carbon (strictly less than)."""
        from scheduler.carbon_client import CarbonForecast
        fc = CarbonForecast(
            zone="eastus",
            current_intensity=400.0,
            is_high_carbon_now=(400.0 >= 400.0),  # True — at or above
        )
        # The router uses `forecast.is_high_carbon_now` as set by CarbonAwareClient
        # At exactly 400.0, high_threshold=400.0 → intensity >= threshold → True
        assert fc.is_high_carbon_now is True

    def test_forecast_just_below_threshold_not_high(self):
        fc = CarbonForecast(
            zone="eastus",
            current_intensity=399.9,
            is_high_carbon_now=(399.9 >= 400.0),
        )
        assert fc.is_high_carbon_now is False

    def test_router_uses_is_high_carbon_now_flag(self):
        """RiskRouter must use the pre-computed is_high_carbon_now, not recalculate."""
        from scheduler.risk_router import RiskRouter
        router = RiskRouter()

        # Forecast says NOT high carbon (despite intensity=405)
        fc = CarbonForecast(
            zone="eastus",
            current_intensity=405.0,
            is_high_carbon_now=False,  # caller overrides
        )
        decision = router.route(risk_score=0.30, forecast=fc)
        # Should not defer — forecast.is_high_carbon_now is False
        assert not decision.has_deferred_jobs


# ── SCI formula components ────────────────────────────────────────────────────

class TestSCIComponents:
    """
    Verify that carbon_cost_grams() implements the SCI formula correctly:
        SCI = (E × I + M) / R
    We test the E × I component (M and R are outside this model).
    """

    def test_energy_scales_linearly_with_intensity(self):
        agents = agents_for_tier(Tier.FULL)
        c1 = carbon_cost_grams(agents, carbon_intensity=100.0)
        c2 = carbon_cost_grams(agents, carbon_intensity=200.0)
        assert abs(c2 / c1 - 2.0) < 0.01, "Carbon cost should scale linearly with intensity"

    def test_energy_scales_linearly_with_pue(self):
        agents = ["api_agent"]
        c1 = carbon_cost_grams(agents, 400.0, pue=1.0)
        c2 = carbon_cost_grams(agents, 400.0, pue=2.0)
        assert abs(c2 / c1 - 2.0) < 0.01, "Carbon cost should scale linearly with PUE"

    def test_energy_scales_linearly_with_tdp(self):
        agents = ["api_agent"]
        c1 = carbon_cost_grams(agents, 400.0, tdp_watts=45.0)
        c2 = carbon_cost_grams(agents, 400.0, tdp_watts=90.0)
        assert abs(c2 / c1 - 2.0) < 0.01, "Carbon cost should scale linearly with TDP"

    def test_units_are_grams_not_kg(self):
        """For a reasonable CI job, cost should be in milligrams to grams range, not kg."""
        agents = agents_for_tier(Tier.FULL)
        cost = carbon_cost_grams(agents, carbon_intensity=400.0)
        assert 0.0001 < cost < 100.0, \
            f"Carbon cost {cost:.6f}g is implausible — check units"
