# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
test_risk_router.py
===================
Unit tests for RiskRouter routing logic.
No external dependencies — uses mock CarbonForecast objects.
"""

from datetime import datetime, timedelta, timezone

import pytest

from scheduler.carbon_client import CarbonForecast, CarbonWindow
from scheduler.risk_router import RiskBucket, RiskRouter, classify_risk


def _make_forecast(
    intensity: float = 300.0,
    high_threshold: float = 400.0,
    optimal_intensity: float = 150.0,
    hours_ahead: float = 2.0,
) -> CarbonForecast:
    now = datetime.now(timezone.utc)
    optimal = CarbonWindow(
        zone="eastus",
        start=now + timedelta(hours=hours_ahead),
        end=now + timedelta(hours=hours_ahead + 0.5),
        intensity=optimal_intensity,
        is_optimal=True,
    )
    return CarbonForecast(
        zone="eastus",
        current_intensity=intensity,
        windows=[optimal],
        optimal_window=optimal,
        is_high_carbon_now=(intensity >= high_threshold),
    )


class TestClassifyRisk:
    def test_low(self):
        assert classify_risk(0.00) == RiskBucket.LOW
        assert classify_risk(0.39) == RiskBucket.LOW

    def test_medium(self):
        assert classify_risk(0.40) == RiskBucket.MEDIUM
        assert classify_risk(0.69) == RiskBucket.MEDIUM

    def test_high(self):
        assert classify_risk(0.70) == RiskBucket.HIGH
        assert classify_risk(0.89) == RiskBucket.HIGH

    def test_critical(self):
        assert classify_risk(0.90) == RiskBucket.CRITICAL
        assert classify_risk(1.00) == RiskBucket.CRITICAL


class TestRiskRouter:
    def setup_method(self):
        self.router = RiskRouter()

    # ── CRITICAL ─────────────────────────────────────────────────────────────

    def test_critical_no_deferral(self):
        forecast = _make_forecast(intensity=500.0)  # high carbon
        decision = self.router.route(risk_score=0.95, forecast=forecast, pr_id="PR-1")
        assert decision.risk_bucket == RiskBucket.CRITICAL
        assert not decision.has_deferred_jobs
        assert len(decision.immediate_agents) > 0

    def test_force_full_overrides_carbon(self):
        forecast = _make_forecast(intensity=600.0)
        decision = self.router.route(risk_score=0.30, forecast=forecast, force_full=True)
        assert not decision.has_deferred_jobs
        assert "perf_agent" in decision.immediate_agents

    # ── LOW carbon — no deferral regardless of risk ───────────────────────────

    def test_low_carbon_no_deferral(self):
        forecast = _make_forecast(intensity=200.0)  # below threshold of 400
        decision = self.router.route(risk_score=0.55, forecast=forecast)
        assert not decision.has_deferred_jobs

    # ── HIGH carbon + LOW risk ────────────────────────────────────────────────

    def test_low_risk_high_carbon_defers_medium(self):
        forecast = _make_forecast(intensity=500.0)
        decision = self.router.route(risk_score=0.30, forecast=forecast, pr_id="PR-2")
        assert decision.risk_bucket == RiskBucket.LOW
        # Cheap agents run immediately
        assert len(decision.immediate_agents) > 0
        # FULL tier never scheduled for LOW risk
        assert "perf_agent" not in decision.immediate_agents
        assert "perf_agent" not in decision.deferred_agents

    def test_low_risk_immediate_agents_are_cheap_only(self):
        from scheduler.cost_model import AGENT_COSTS, Tier
        forecast = _make_forecast(intensity=500.0)
        decision = self.router.route(risk_score=0.20, forecast=forecast)
        for agent in decision.immediate_agents:
            assert AGENT_COSTS[agent].tier == Tier.CHEAP, \
                f"{agent} is not a CHEAP agent but appears in immediate_agents"

    # ── HIGH carbon + HIGH risk ───────────────────────────────────────────────

    def test_high_risk_defers_perf_and_browser(self):
        forecast = _make_forecast(intensity=550.0)
        decision = self.router.route(risk_score=0.80, forecast=forecast)
        assert "perf_agent"    in decision.deferred_agents
        assert "browser_agent" in decision.deferred_agents
        assert "playwright_agent" in decision.immediate_agents

    def test_high_risk_deferred_window_set(self):
        forecast = _make_forecast(intensity=550.0, optimal_intensity=120.0)
        decision = self.router.route(risk_score=0.80, forecast=forecast)
        assert decision.deferred_window is not None
        assert decision.deferred_window.intensity < 200.0

    # ── CO2 savings ──────────────────────────────────────────────────────────

    def test_co2_savings_positive_when_deferred(self):
        forecast = _make_forecast(intensity=600.0, optimal_intensity=80.0)
        decision = self.router.route(risk_score=0.80, forecast=forecast)
        if decision.has_deferred_jobs:
            assert decision.estimated_savings_g_co2 > 0.0

    # ── SLA deadline ─────────────────────────────────────────────────────────

    def test_sla_deadline_respected(self):
        now      = datetime.now(timezone.utc)
        deadline = now + timedelta(hours=1)
        # Optimal window is 2 hours away — beyond the deadline
        forecast = _make_forecast(intensity=500.0, hours_ahead=2.0)
        decision = self.router.route(risk_score=0.80, forecast=forecast, sla_deadline=deadline)
        if decision.deferred_window:
            assert decision.deferred_window.start < deadline
