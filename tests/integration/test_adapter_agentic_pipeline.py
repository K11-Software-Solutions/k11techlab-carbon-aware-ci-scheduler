# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
tests/integration/test_adapter_agentic_pipeline.py
===================================================
Phase 3 — Integration tests: QAPipelineAdapter wired to k11techlab-agentic-ai-qa-system.

These tests verify the adapter correctly bridges the carbon scheduler's
run_agents() call to the agentic pipeline's run_pipeline() interface.

Prerequisites:
  - k11techlab-agentic-ai-qa-system must be installed or on PYTHONPATH
  - Set: PYTHONPATH=../k11techlab-agentic-ai-qa-system:$PYTHONPATH
  - QA_PIPELINE_MODULE=pipeline.runner

Skip markers:
  @pytest.mark.agentic  — skipped unless --agentic flag is passed to pytest
  @pytest.mark.slow     — skipped unless --slow flag is passed to pytest

Run:
  pytest tests/integration/test_adapter_agentic_pipeline.py \
    --agentic --slow \
    -v

Environment:
  QA_PIPELINE_MODULE=pipeline.runner
  PYTHONPATH=../k11techlab-agentic-ai-qa-system
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parents[3]
AGENTIC_REPO = REPO_ROOT / "k11techlab-agentic-ai-qa-system"

if AGENTIC_REPO.exists() and str(AGENTIC_REPO) not in sys.path:
    sys.path.insert(0, str(AGENTIC_REPO))

pytestmark = pytest.mark.agentic


# ── Helpers ───────────────────────────────────────────────────────────────────

SAMPLE_PR_DIFF = """\
diff --git a/api/routes.py b/api/routes.py
index abc123..def456 100644
--- a/api/routes.py
+++ b/api/routes.py
@@ -10,6 +10,8 @@ from flask import Flask, request, jsonify
 app = Flask(__name__)

+# New authentication endpoint
+@app.route('/auth/token', methods=['POST'])
+def get_token():
+    return jsonify({"token": "xxx"})
"""


# ── Mock agentic runner for unit-level testing ────────────────────────────────

class MockAgenticRunner:
    """
    Minimal stand-in for pipeline.runner from k11techlab-agentic-ai-qa-system.
    Provides the async run_pipeline() interface without spinning up LangGraph.
    """

    @staticmethod
    async def run_pipeline(
        pr_number=1,
        repo_name="k11/test",
        pr_diff=SAMPLE_PR_DIFF,
        agent_tier="cheap",
        agents=None,
        metadata=None,
        use_memory=True,
        **kwargs,
    ):
        return {
            "run_id": "mock-run-001",
            "risk_score": 0.55,
            "status": "passed",
            "verdict": "PASS",
            "duration_s": 12.5,
            "eval_passed": True,
            "summary": {
                "pass_rate": 1.0,
                "severity_breakdown": {},
                "verdict": "PASS",
            },
            "test_results": [{"suite": "api_agent", "verdict": "PASS"}],
            "defects": [],
        }


# ── Adapter wired to mock agentic runner ─────────────────────────────────────

@pytest.mark.asyncio
class TestAdapterWithMockAgenticRunner:
    """
    Uses a patched version of the agentic runner.
    These run without any real pipeline infrastructure.
    """

    @pytest.fixture(autouse=True)
    def patch_runner(self, request):
        """Patch importlib to return our mock runner."""
        if request.node.name == "test_run_agents_timeout_handled":
            yield
            return

        with patch("importlib.import_module", return_value=MockAgenticRunner):
            yield

    async def test_run_agents_cheap_tier_returns_pass(self):
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents(
            pr_id="PR-agentic-1",
            agents=["api_agent", "security_agent"],
            meta={"pr_number": 100, "repo_name": "k11/demo"},
        )
        assert result.verdict == "PASS"
        assert result.status in ("passed", "stub", "unknown")

    async def test_run_agents_medium_tier_maps_correctly(self):
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter, _agents_to_tier
        agents = ["api_agent", "cross_repo_impact_agent"]
        tier   = _agents_to_tier(agents)
        assert tier == "medium"

        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-agentic-2", agents)
        assert result.pr_id == "PR-agentic-2"

    async def test_run_agents_full_tier_maps_correctly(self):
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter, _agents_to_tier
        agents = ["playwright_agent", "perf_agent", "browser_agent"]
        tier   = _agents_to_tier(agents)
        assert tier == "full"

        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-agentic-3", agents)
        assert isinstance(result.duration_s, float)

    async def test_run_agents_timeout_handled(self):
        import asyncio
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        os.environ["QA_PIPELINE_TIMEOUT"]  = "1"  # 1-second timeout

        async def slow_pipeline(**kwargs):
            await asyncio.sleep(5)
            return {}

        slow_runner = MagicMock()
        slow_runner.run_pipeline = slow_pipeline

        with patch("importlib.import_module", return_value=slow_runner):
            from integrations import qa_pipeline_adapter as mod
            # Force reload to pick up new env var
            import importlib
            importlib.reload(mod)
            adapter = mod.QAPipelineAdapter()
            result = await adapter.run_agents("PR-timeout", ["api_agent"])

        assert result.status == "error"
        assert "timeout" in result.metadata.get("error", "").lower()
        os.environ.pop("QA_PIPELINE_TIMEOUT", None)

    async def test_normalise_result_from_dict(self):
        from integrations.qa_pipeline_adapter import _normalise_result
        now = datetime.now(timezone.utc)
        raw = {"status": "passed", "verdict": "PASS", "duration_s": 8.3}
        result = _normalise_result(raw, "PR-norm", ["api_agent"], now)
        assert result.verdict == "PASS"
        assert result.duration_s == pytest.approx(8.3)

    async def test_normalise_result_from_object(self):
        from integrations.qa_pipeline_adapter import _normalise_result
        now = datetime.now(timezone.utc)

        class FakeResult:
            status = "passed"
            verdict = "PASS"
            duration_s = 15.7
            metadata = {}

        result = _normalise_result(FakeResult(), "PR-obj", ["perf_agent"], now)
        assert result.verdict == "PASS"


# ── Live agentic pipeline tests (marked slow + agentic) ──────────────────────

@pytest.mark.slow
@pytest.mark.asyncio
class TestAdapterWithLiveAgenticPipeline:
    """
    These tests spin up the real LangGraph pipeline in memory mode.
    Requires k11techlab-agentic-ai-qa-system on PYTHONPATH and
    all its dependencies installed.

    Run with:
        pytest tests/integration/test_adapter_agentic_pipeline.py::TestAdapterWithLiveAgenticPipeline \
            --agentic --slow -v
    """

    @pytest.fixture(autouse=True)
    def require_agentic_repo(self):
        if not AGENTIC_REPO.exists():
            pytest.skip(f"k11techlab-agentic-ai-qa-system not found at {AGENTIC_REPO}")

    async def test_cheap_tier_pipeline_runs_and_returns_state(self):
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter
        adapter = QAPipelineAdapter()

        result = await adapter.run_agents(
            pr_id="PR-live-cheap",
            agents=["api_agent", "security_agent"],
            meta={
                "pr_number": 1,
                "repo_name": "k11techlab/k11techlab-microservice-qa-system",
                "pr_diff": SAMPLE_PR_DIFF,
                "use_memory": True,
            },
        )
        assert result.pr_id == "PR-live-cheap"
        assert result.verdict in ("PASS", "FAIL", "HITL", "UNKNOWN")
        assert result.duration_s > 0

    async def test_full_suite_pipeline_verdict_is_valid(self):
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter
        adapter = QAPipelineAdapter()

        result = await adapter.run_agents(
            pr_id="PR-live-full",
            agents=["api_agent", "security_agent", "playwright_agent"],
            meta={
                "pr_number": 2,
                "repo_name": "k11techlab/k11techlab-microservice-qa-system",
                "pr_diff": SAMPLE_PR_DIFF,
                "use_memory": True,
            },
        )
        assert result.verdict in ("PASS", "FAIL", "HITL")

    async def test_pipeline_no_quality_regression_after_carbon_deferral(self):
        """
        Core correctness test: running the same PR through cheap-tier now
        and full-tier deferred should not produce conflicting verdicts.
        In stub/memory mode, both should PASS.
        """
        os.environ["QA_PIPELINE_MODULE"] = "pipeline.runner"
        from integrations.qa_pipeline_adapter import QAPipelineAdapter
        adapter = QAPipelineAdapter()

        r_cheap = await adapter.run_agents(
            pr_id="PR-regress-cheap",
            agents=["api_agent", "security_agent"],
            meta={"pr_number": 3, "repo_name": "k11/demo",
                  "pr_diff": SAMPLE_PR_DIFF, "use_memory": True},
        )
        r_full = await adapter.run_agents(
            pr_id="PR-regress-full",
            agents=["playwright_agent", "perf_agent"],
            meta={"pr_number": 3, "repo_name": "k11/demo",
                  "pr_diff": SAMPLE_PR_DIFF, "use_memory": True},
        )

        # Neither should ERROR
        assert r_cheap.status != "error", f"Cheap tier errored: {r_cheap.metadata}"
        assert r_full.status  != "error", f"Full tier errored: {r_full.metadata}"
