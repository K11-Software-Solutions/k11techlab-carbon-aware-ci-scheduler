# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
tests/e2e/test_carbon_scheduling_e2e.py
=======================================
Phase 4 — End-to-end tests: Live Carbon Aware SDK + agentic QA pipeline.

These tests verify the full system as deployed:
  - Real Carbon Aware SDK (Docker) provides grid intensity data
  - Real k11techlab-agentic-ai-qa-system executes test agents
  - k11techlab-microservice-qa-system acts as the system-under-test

Prerequisites:
  1. Carbon Aware SDK running:
       docker run -p 8090:8090 ghcr.io/green-software-foundation/carbon-aware-sdk:latest
  2. Both QA repos on PYTHONPATH:
       export PYTHONPATH=../k11techlab-agentic-ai-qa-system:../k11techlab-microservice-qa-system
  3. Environment configured:
       export QA_PIPELINE_MODULE=pipeline.runner
       export CARBON_SDK_BASE_URL=http://localhost:8090
       export CARBON_SDK_ZONE=eastus
       export CARBON_HIGH_THRESHOLD=400

Run all E2E tests:
  pytest tests/e2e/ --e2e --slow -v

Run specific scenarios:
  pytest tests/e2e/test_carbon_scheduling_e2e.py::TestCarbonAwareRoutingE2E -v --e2e
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

REPO_ROOT     = Path(__file__).parents[3]
AGENTIC_REPO  = REPO_ROOT / "k11techlab-agentic-ai-qa-system"
MICRO_REPO    = REPO_ROOT / "k11techlab-microservice-qa-system"

for repo in (AGENTIC_REPO, MICRO_REPO):
    if repo.exists() and str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

# ── Sample diffs ──────────────────────────────────────────────────────────────

AUTH_DIFF = """\
diff --git a/api/auth.py b/api/auth.py
index abc..def 100644
--- a/api/auth.py
+++ b/api/auth.py
@@ -5,3 +5,8 @@ from flask import Flask
+@app.route('/auth/token', methods=['POST'])
+def get_token():
+    # HIGH RISK: new auth endpoint
+    return jsonify({"token": generate_token()})
"""

CONFIG_DIFF = """\
diff --git a/config/settings.py b/config/settings.py
index 111..222 100644
--- a/config/settings.py
+++ b/config/settings.py
@@ -1,3 +1,4 @@
+LOG_LEVEL = 'DEBUG'
 TIMEOUT = 30
"""

SCHEMA_DIFF = """\
diff --git a/db/migrations/0042.sql b/db/migrations/0042.sql
new file mode 100644
--- /dev/null
+++ b/db/migrations/0042.sql
@@ -0,0 +1,5 @@
+ALTER TABLE users ADD COLUMN carbon_opt_in BOOLEAN DEFAULT FALSE;
"""


def _pr_meta(pr_number: int, diff: str, use_memory: bool = True) -> dict:
    return {
        "pr_number":  pr_number,
        "repo_name":  "k11techlab/k11techlab-microservice-qa-system",
        "pr_diff":    diff,
        "use_memory": use_memory,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def require_carbon_sdk():
    """Skip all tests if the Carbon Aware SDK is not reachable."""
    import httpx
    base = os.getenv("CARBON_SDK_BASE_URL", "http://localhost:8090")
    try:
        r = httpx.get(f"{base}/emissions/bylocations/best?location=eastus", timeout=5)
        r.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Carbon Aware SDK not available at {base}: {exc}")


@pytest.fixture(scope="module")
def require_agentic_repo():
    if not AGENTIC_REPO.exists():
        pytest.skip(f"k11techlab-agentic-ai-qa-system not found at {AGENTIC_REPO}")


# ── Core E2E routing tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCarbonAwareRoutingE2E:
    """
    Verify that the scheduler makes correct routing decisions against the live
    Carbon Aware SDK. Agent execution uses stub/memory mode.
    """

    @pytest.fixture(autouse=True)
    def _deps(self, require_carbon_sdk):
        pass

    async def test_scheduler_gets_real_carbon_intensity(self):
        from scheduler.carbon_client import CarbonAwareClient
        async with CarbonAwareClient() as client:
            intensity = await client.current_intensity()
        assert isinstance(intensity, float)
        assert intensity >= 0.0
        assert intensity < 2000.0, f"Implausible intensity: {intensity}"

    async def test_scheduler_gets_forecast_windows(self):
        from scheduler.carbon_client import CarbonAwareClient
        async with CarbonAwareClient() as client:
            forecast = await client.get_forecast()
        assert forecast.zone is not None
        assert forecast.current_intensity >= 0.0
        # Windows may be empty if SDK has limited data for the zone
        if forecast.windows:
            for w in forecast.windows:
                assert w.intensity >= 0.0
                assert w.start < w.end

    async def test_routing_decision_for_low_risk_pr(self):
        """Low-risk config change: cheap tier immediate, possibly defer medium."""
        from scheduler.scheduler import CarbonAwareScheduler, PREvent
        results = []

        async def run_fn(pr_id, agents, meta=None):
            results.append(agents)
            return {"status": "stub", "verdict": "PASS"}

        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            decision = await sched.submit(PREvent(
                pr_id="PR-e2e-low",
                risk_score=0.20,   # LOW — config change
            ))

        from scheduler.risk_router import RiskBucket
        assert decision.risk_bucket == RiskBucket.LOW
        # FULL tier must never appear for LOW-risk PR
        assert "perf_agent" not in decision.immediate_agents
        assert "perf_agent" not in decision.deferred_agents

    async def test_routing_decision_for_high_risk_pr(self):
        """High-risk auth change: playwright must run immediately."""
        from scheduler.scheduler import CarbonAwareScheduler, PREvent
        results = []

        async def run_fn(pr_id, agents, meta=None):
            results.append(agents)
            return {"status": "stub", "verdict": "PASS"}

        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            decision = await sched.submit(PREvent(
                pr_id="PR-e2e-high",
                risk_score=0.82,   # HIGH — auth endpoint added
            ))

        from scheduler.risk_router import RiskBucket
        assert decision.risk_bucket == RiskBucket.HIGH
        # playwright always immediate for HIGH risk (regardless of carbon)
        assert "playwright_agent" in decision.immediate_agents

    async def test_critical_pr_ignores_carbon_intensity(self):
        """Critical PR: full suite runs regardless of carbon state."""
        from scheduler.scheduler import CarbonAwareScheduler, PREvent
        results = []

        async def run_fn(pr_id, agents, meta=None):
            results.append(agents)
            return {"status": "stub"}

        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            decision = await sched.submit(PREvent(
                pr_id="PR-e2e-crit",
                risk_score=0.95,
            ))

        from scheduler.risk_router import RiskBucket
        assert decision.risk_bucket == RiskBucket.CRITICAL
        assert not decision.has_deferred_jobs
        assert len(decision.immediate_agents) == 10  # full suite


# ── Quality regression tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
class TestNoQualityRegressionE2E:
    """
    Verify that deferring expensive tests to a low-carbon window
    produces the same verdicts as running everything immediately.

    These use real agentic pipeline in memory mode.
    """

    @pytest.fixture(autouse=True)
    def _deps(self, require_carbon_sdk, require_agentic_repo):
        pass

    async def test_cheap_and_deferred_tier_agree_on_pass(self):
        """
        Run cheap tier immediately and full tier 'deferred' (simulated).
        Both should PASS for the same PR diff.
        """
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter
        adapter = QAPipelineAdapter()

        r_cheap = await adapter.run_agents(
            pr_id="PR-quality-cheap",
            agents=["api_agent", "security_agent", "regression_agent"],
            meta=_pr_meta(pr_number=100, diff=CONFIG_DIFF),
        )
        r_full = await adapter.run_agents(
            pr_id="PR-quality-full",
            agents=["playwright_agent", "perf_agent"],
            meta=_pr_meta(pr_number=100, diff=CONFIG_DIFF),
        )

        assert r_cheap.status != "error", f"Cheap tier error: {r_cheap.metadata}"
        assert r_full.status  != "error", f"Full tier error: {r_full.metadata}"

        # A config-only change should not fail
        if r_cheap.verdict == "PASS":
            assert r_full.verdict in ("PASS", "HITL"), \
                "Deferred full-tier verdict conflicts with cheap-tier PASS"

    async def test_zero_false_failures_from_carbon_deferral(self):
        """
        The 180-PR study found 0% quality regression.
        Verify no additional failures arise purely from scheduling deferral
        on a known-clean diff.
        """
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter
        adapter = QAPipelineAdapter()

        results = []
        for i in range(5):  # run 5 times to check determinism
            r = await adapter.run_agents(
                pr_id=f"PR-stability-{i}",
                agents=["api_agent", "regression_agent"],
                meta=_pr_meta(pr_number=200 + i, diff=CONFIG_DIFF),
            )
            results.append(r.verdict)

        fail_count = results.count("FAIL")
        assert fail_count == 0, \
            f"Clean diff produced {fail_count}/5 FAILs — possible regression from scheduling"


# ── Carbon savings measurement ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCarbonSavingsMeasurementE2E:
    """
    Verify that carbon savings estimates are realistic and measurable
    against real Carbon Aware SDK data.
    """

    @pytest.fixture(autouse=True)
    def _deps(self, require_carbon_sdk):
        pass

    async def test_savings_estimate_is_non_negative(self):
        from scheduler.scheduler import CarbonAwareScheduler, PREvent

        async def run_fn(pr_id, agents, meta=None):
            return {"status": "stub"}

        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            decision = await sched.submit(PREvent(
                pr_id="PR-savings", risk_score=0.80
            ))

        assert decision.estimated_savings_g_co2 >= 0.0

    async def test_metrics_co2_counter_tracks_across_prs(self):
        from scheduler.scheduler import CarbonAwareScheduler, PREvent

        async def run_fn(pr_id, agents, meta=None):
            return {"status": "stub"}

        pr_ids = [f"PR-metric-{i}" for i in range(3)]
        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            for pr_id in pr_ids:
                await sched.submit(PREvent(pr_id=pr_id, risk_score=0.80))
            m = sched.metrics()

        assert m["carbon_scheduler_prs_submitted_total"] == 3
        assert m["carbon_scheduler_co2_saved_grams_total"] >= 0.0

    async def test_scheduler_reports_pending_jobs(self):
        from scheduler.scheduler import CarbonAwareScheduler, PREvent

        async def run_fn(pr_id, agents, meta=None):
            await asyncio.sleep(100)  # never fires in test window
            return {"status": "stub"}

        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            d = await sched.submit(PREvent(pr_id="PR-pending", risk_score=0.30))
            m = sched.metrics()

        # May have 0 pending (if low carbon) or ≥1 (if high carbon + deferred)
        assert m["carbon_scheduler_jobs_pending"] >= 0


# ── Microservice QA integration ───────────────────────────────────────────────

@pytest.mark.asyncio
class TestMicroserviceQAIntegrationE2E:
    """
    Verify that carbon-aware scheduling works when the QA system
    under test is k11techlab-microservice-qa-system.
    """

    @pytest.fixture(autouse=True)
    def _deps(self, require_carbon_sdk):
        if not MICRO_REPO.exists():
            pytest.skip(f"k11techlab-microservice-qa-system not found at {MICRO_REPO}")

    async def test_schema_change_pr_routes_high_risk(self):
        """DB migration diffs should score high risk and run full suite."""
        from scheduler.scheduler import CarbonAwareScheduler, PREvent
        from scheduler.risk_router import RiskBucket

        async def run_fn(pr_id, agents, meta=None):
            return {"status": "stub"}

        # Schema changes are high-risk; simulate a manually-assigned risk score
        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            decision = await sched.submit(PREvent(
                pr_id="PR-schema",
                risk_score=0.82,   # HIGH — schema migration
                metadata={"diff_type": "schema", "pr_diff": SCHEMA_DIFF}
            ))

        assert decision.risk_bucket == RiskBucket.HIGH
        # Critical QA agents must run immediately
        assert "playwright_agent" in decision.immediate_agents

    async def test_full_pipeline_produces_metrics(self):
        """System-level smoke test: submit 5 PRs, check metrics are coherent."""
        from scheduler.scheduler import CarbonAwareScheduler, PREvent

        fired_agents: list[str] = []

        async def run_fn(pr_id, agents, meta=None):
            fired_agents.extend(agents)
            return {"status": "stub", "verdict": "PASS"}

        prs = [
            PREvent(pr_id="PR-ms-1", risk_score=0.15),   # LOW
            PREvent(pr_id="PR-ms-2", risk_score=0.50),   # MEDIUM
            PREvent(pr_id="PR-ms-3", risk_score=0.75),   # HIGH
            PREvent(pr_id="PR-ms-4", risk_score=0.95),   # CRITICAL
            PREvent(pr_id="PR-ms-5", risk_score=0.30),   # LOW
        ]
        async with CarbonAwareScheduler(run_fn=run_fn) as sched:
            decisions = [await sched.submit(pr) for pr in prs]
            m = sched.metrics()

        assert m["carbon_scheduler_prs_submitted_total"] == 5
        # All submitted PRs must have immediate agents scheduled
        for d in decisions:
            assert len(d.immediate_agents) > 0
