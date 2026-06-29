# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
tests/integration/test_scheduler_submit.py
==========================================
Phase 2 — Integration tests for full scheduler submit() flow with stub adapter.

Tests the entire chain:
  PREvent → CarbonAwareScheduler → RiskRouter → DeferEngine → stub run_fn

Carbon Aware SDK is mocked via respx. No real external services required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import respx
import httpx

from scheduler.carbon_client import CarbonAwareClient
from scheduler.risk_router import RiskBucket
from scheduler.scheduler import CarbonAwareScheduler, PREvent

BASE_URL = "http://localhost:8090"


# ── HTTP mock helpers ─────────────────────────────────────────────────────────

def _mock_intensity(mock, rating: float, zone: str = "eastus"):
    mock.get("/emissions/bylocations/best").mock(
        return_value=httpx.Response(200, json=[{"location": zone, "rating": rating}])
    )


def _mock_forecast(mock, points: list[tuple[float, float]], zone: str = "eastus"):
    from scheduler.carbon_client import datetime as dt_module
    import json
    now = datetime.now(timezone.utc)
    data = [{
        "location": zone,
        "optimalDataPoints": [
            {"timestamp": (now + timedelta(hours=h)).isoformat(), "value": v}
            for h, v in points
        ]
    }]
    mock.get("/emissions/forecasts/current").mock(
        return_value=httpx.Response(200, json=data)
    )


# ── All risk buckets × carbon states ─────────────────────────────────────────

@pytest.mark.asyncio
class TestAllRoutingScenarios:
    """8 routing scenarios: 4 risk buckets × 2 carbon states."""

    async def _run(self, risk_score: float, carbon_intensity: float) -> dict:
        results = []

        async def run_fn(pr_id, agents, meta=None):
            results.append({"pr_id": pr_id, "agents": agents})
            return {"status": "stub", "verdict": "PASS"}

        with respx.mock(base_url=BASE_URL) as mock:
            _mock_intensity(mock, carbon_intensity)
            _mock_forecast(mock, [(2.0, 80.0), (3.0, 120.0)])

            client = CarbonAwareClient(base_url=BASE_URL)
            sched  = CarbonAwareScheduler(carbon_client=client, run_fn=run_fn)
            sched.start()
            decision = await sched.submit(PREvent(pr_id="PR-x", risk_score=risk_score))
            sched.stop()
            await client.close()

        return {
            "decision": decision,
            "run_calls": results,
        }

    # LOW risk

    async def test_low_risk_low_carbon_no_deferral(self):
        r = await self._run(risk_score=0.20, carbon_intensity=200.0)
        assert r["decision"].risk_bucket == RiskBucket.LOW
        assert not r["decision"].has_deferred_jobs

    async def test_low_risk_high_carbon_defers_medium(self):
        r = await self._run(risk_score=0.20, carbon_intensity=500.0)
        assert r["decision"].risk_bucket == RiskBucket.LOW
        assert r["decision"].has_deferred_jobs
        # FULL tier never deferred for LOW risk
        assert "perf_agent" not in r["decision"].deferred_agents

    # MEDIUM risk

    async def test_medium_risk_low_carbon_no_deferral(self):
        r = await self._run(risk_score=0.55, carbon_intensity=200.0)
        assert r["decision"].risk_bucket == RiskBucket.MEDIUM
        assert not r["decision"].has_deferred_jobs

    async def test_medium_risk_high_carbon_defers_medium(self):
        r = await self._run(risk_score=0.55, carbon_intensity=500.0)
        assert r["decision"].risk_bucket == RiskBucket.MEDIUM
        assert r["decision"].has_deferred_jobs

    # HIGH risk

    async def test_high_risk_low_carbon_runs_full_immediately(self):
        r = await self._run(risk_score=0.80, carbon_intensity=200.0)
        assert r["decision"].risk_bucket == RiskBucket.HIGH
        assert not r["decision"].has_deferred_jobs
        assert "perf_agent" in r["decision"].immediate_agents

    async def test_high_risk_high_carbon_defers_perf_browser(self):
        r = await self._run(risk_score=0.80, carbon_intensity=500.0)
        assert r["decision"].risk_bucket == RiskBucket.HIGH
        deferred = set(r["decision"].deferred_agents)
        assert "perf_agent"    in deferred
        assert "browser_agent" in deferred
        assert "playwright_agent" in r["decision"].immediate_agents

    # CRITICAL risk

    async def test_critical_risk_low_carbon_no_deferral(self):
        r = await self._run(risk_score=0.95, carbon_intensity=200.0)
        assert r["decision"].risk_bucket == RiskBucket.CRITICAL
        assert not r["decision"].has_deferred_jobs

    async def test_critical_risk_high_carbon_no_deferral(self):
        r = await self._run(risk_score=0.95, carbon_intensity=600.0)
        assert r["decision"].risk_bucket == RiskBucket.CRITICAL
        assert not r["decision"].has_deferred_jobs
        assert "perf_agent" in r["decision"].immediate_agents


# ── CO2 savings ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCO2Savings:
    async def test_co2_savings_positive_for_deferred_jobs(self):
        async def run_fn(pr_id, agents, meta=None):
            return {"status": "stub"}

        with respx.mock(base_url=BASE_URL) as mock:
            _mock_intensity(mock, 600.0)
            _mock_forecast(mock, [(2.0, 50.0)])

            client = CarbonAwareClient(base_url=BASE_URL)
            sched  = CarbonAwareScheduler(carbon_client=client, run_fn=run_fn)
            sched.start()
            decision = await sched.submit(PREvent(pr_id="PR-co2", risk_score=0.80))
            sched.stop()
            await client.close()

        if decision.has_deferred_jobs:
            assert decision.estimated_savings_g_co2 > 0.0

    async def test_no_savings_when_carbon_low(self):
        async def run_fn(pr_id, agents, meta=None):
            return {"status": "stub"}

        with respx.mock(base_url=BASE_URL) as mock:
            _mock_intensity(mock, 200.0)
            _mock_forecast(mock, [(2.0, 180.0)])

            client = CarbonAwareClient(base_url=BASE_URL)
            sched  = CarbonAwareScheduler(carbon_client=client, run_fn=run_fn)
            sched.start()
            decision = await sched.submit(PREvent(pr_id="PR-nosc", risk_score=0.80))
            sched.stop()
            await client.close()

        assert not decision.has_deferred_jobs


# ── Multiple PRs and cancel ───────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMultiplePRs:
    async def test_multiple_prs_tracked_independently(self):
        async def run_fn(pr_id, agents, meta=None):
            return {"status": "stub"}

        with respx.mock(base_url=BASE_URL) as mock:
            _mock_intensity(mock, 500.0)
            _mock_forecast(mock, [(2.0, 80.0)])

            client = CarbonAwareClient(base_url=BASE_URL)
            sched  = CarbonAwareScheduler(carbon_client=client, run_fn=run_fn)
            sched.start()

            d1 = await sched.submit(PREvent(pr_id="PR-A", risk_score=0.20))
            d2 = await sched.submit(PREvent(pr_id="PR-B", risk_score=0.95))
            d3 = await sched.submit(PREvent(pr_id="PR-C", risk_score=0.75))

            sched.stop()
            await client.close()

        assert d1.risk_bucket == RiskBucket.LOW
        assert d2.risk_bucket == RiskBucket.CRITICAL
        assert d3.risk_bucket == RiskBucket.HIGH

    async def test_cancel_pr_after_submit(self):
        async def run_fn(pr_id, agents, meta=None):
            return {"status": "stub"}

        with respx.mock(base_url=BASE_URL) as mock:
            _mock_intensity(mock, 500.0)
            _mock_forecast(mock, [(2.0, 80.0)])

            client = CarbonAwareClient(base_url=BASE_URL)
            sched  = CarbonAwareScheduler(carbon_client=client, run_fn=run_fn)
            sched.start()

            decision = await sched.submit(PREvent(pr_id="PR-del", risk_score=0.20))
            if decision.has_deferred_jobs:
                cancelled = sched.cancel_pr("PR-del")
                assert cancelled >= 1

            sched.stop()
            await client.close()
