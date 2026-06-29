# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
tests/integration/test_adapter_stub.py
=======================================
Phase 2 — Integration tests for QAPipelineAdapter in stub mode.

These tests verify adapter behaviour when the QA pipeline module
is not installed (or the env var points to a non-existent module).
No real QA pipeline required.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from integrations.qa_pipeline_adapter import QAPipelineAdapter, PipelineResult, _agents_to_tier
from scheduler.cost_model import Tier


# ── Stub mode ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAdapterStubMode:
    def setup_method(self):
        os.environ["QA_PIPELINE_MODULE"] = "nonexistent.module.that.does.not.exist"

    def teardown_method(self):
        os.environ.pop("QA_PIPELINE_MODULE", None)

    async def test_stub_returns_pipeline_result(self):
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-1", ["api_agent", "security_agent"])
        assert isinstance(result, PipelineResult)

    async def test_stub_status_is_stub(self):
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-2", ["api_agent"])
        assert result.status == "stub"

    async def test_stub_verdict_is_pass(self):
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-3", ["api_agent"])
        assert result.verdict == "PASS"

    async def test_stub_pr_id_preserved(self):
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-stub-42", ["api_agent"])
        assert result.pr_id == "PR-stub-42"

    async def test_stub_agents_preserved(self):
        adapter = QAPipelineAdapter()
        agents = ["api_agent", "regression_agent", "perf_agent"]
        result = await adapter.run_agents("PR-4", agents)
        assert set(result.agents_run) == set(agents)

    async def test_stub_metadata_in_result(self):
        adapter = QAPipelineAdapter()
        meta = {"risk_score": 0.45, "risk_bucket": "medium"}
        result = await adapter.run_agents("PR-5", ["api_agent"], meta=meta)
        assert result.metadata.get("stub") is True

    async def test_stub_duration_is_positive(self):
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-6", ["api_agent"])
        assert result.duration_s > 0

    async def test_stub_started_at_is_datetime(self):
        from datetime import datetime
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents("PR-7", ["api_agent"])
        assert isinstance(result.started_at, datetime)


# ── _agents_to_tier() helper ─────────────────────────────────────────────────

class TestAgentsToTier:
    def test_cheap_agents_returns_cheap(self):
        assert _agents_to_tier(["api_agent", "data_agent"]) == "cheap"

    def test_medium_agent_returns_medium(self):
        assert _agents_to_tier(["api_agent", "cross_repo_impact_agent"]) == "medium"

    def test_full_agent_returns_full(self):
        assert _agents_to_tier(["perf_agent", "api_agent"]) == "full"

    def test_all_full_tier(self):
        assert _agents_to_tier(["playwright_agent", "browser_agent"]) == "full"

    def test_unknown_agent_defaults_cheap(self):
        assert _agents_to_tier(["unknown_agent"]) == "cheap"

    def test_empty_list_defaults_cheap(self):
        assert _agents_to_tier([]) == "cheap"
