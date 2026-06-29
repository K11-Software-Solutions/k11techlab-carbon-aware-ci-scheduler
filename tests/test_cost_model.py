# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
test_cost_model.py
==================
Phase 1 — Unit tests for cost_model.py.

Verifies:
  - Agent tier assignments match design
  - agents_for_tier() accumulates tiers correctly
  - estimated_duration_s() uses parallel time for cheap/medium, serial for full
  - carbon_cost_grams() formula is correct
  - total_cost() sums cost scores
"""

import pytest

from scheduler.cost_model import (
    AGENT_COSTS,
    TIER_AGENTS,
    AgentCost,
    Tier,
    agents_for_tier,
    carbon_cost_grams,
    estimated_duration_s,
    total_cost,
)


# ── Agent catalogue sanity ────────────────────────────────────────────────────

class TestAgentCatalogue:
    def test_all_expected_agents_present(self):
        expected = {
            "api_agent", "security_agent", "data_agent", "a11y_agent", "regression_agent",
            "cross_repo_impact_agent", "drift_analysis_agent",
            "playwright_agent", "perf_agent", "browser_agent",
        }
        assert expected == set(AGENT_COSTS.keys())

    def test_cheap_tier_agents(self):
        cheap = set(TIER_AGENTS[Tier.CHEAP])
        assert cheap == {"api_agent", "security_agent", "data_agent", "a11y_agent", "regression_agent"}

    def test_medium_tier_agents(self):
        medium = set(TIER_AGENTS[Tier.MEDIUM])
        assert medium == {"cross_repo_impact_agent", "drift_analysis_agent"}

    def test_full_tier_agents(self):
        full = set(TIER_AGENTS[Tier.FULL])
        assert full == {"playwright_agent", "perf_agent", "browser_agent"}

    def test_cost_scores_in_range(self):
        for name, agent in AGENT_COSTS.items():
            assert 0.0 <= agent.cost_score <= 1.0, \
                f"{name}.cost_score={agent.cost_score} out of [0,1]"

    def test_full_tier_more_expensive_than_cheap(self):
        cheap_avg = sum(a.cost_score for a in AGENT_COSTS.values() if a.tier == Tier.CHEAP) / 5
        full_avg  = sum(a.cost_score for a in AGENT_COSTS.values() if a.tier == Tier.FULL) / 3
        assert full_avg > cheap_avg, "FULL tier should be more expensive than CHEAP on average"

    def test_peak_memory_positive(self):
        for name, agent in AGENT_COSTS.items():
            assert agent.peak_memory_mb > 0, f"{name}.peak_memory_mb must be positive"

    def test_avg_duration_positive(self):
        for name, agent in AGENT_COSTS.items():
            assert agent.avg_duration_s > 0, f"{name}.avg_duration_s must be positive"


# ── agents_for_tier() ─────────────────────────────────────────────────────────

class TestAgentsForTier:
    def test_cheap_returns_only_cheap(self):
        result = set(agents_for_tier(Tier.CHEAP))
        assert result == set(TIER_AGENTS[Tier.CHEAP])

    def test_medium_includes_cheap_and_medium(self):
        result = set(agents_for_tier(Tier.MEDIUM))
        assert set(TIER_AGENTS[Tier.CHEAP]).issubset(result)
        assert set(TIER_AGENTS[Tier.MEDIUM]).issubset(result)
        assert not set(TIER_AGENTS[Tier.FULL]).intersection(result)

    def test_full_includes_all_tiers(self):
        result = set(agents_for_tier(Tier.FULL))
        for tier in Tier:
            assert set(TIER_AGENTS[tier]).issubset(result), \
                f"FULL should include {tier.value} agents"

    def test_no_duplicates(self):
        for tier in Tier:
            result = agents_for_tier(tier)
            assert len(result) == len(set(result)), f"Duplicates in agents_for_tier({tier})"


# ── total_cost() ──────────────────────────────────────────────────────────────

class TestTotalCost:
    def test_empty_list_returns_zero(self):
        assert total_cost([]) == 0.0

    def test_unknown_agent_ignored(self):
        assert total_cost(["nonexistent_agent"]) == 0.0

    def test_full_suite_higher_than_cheap(self):
        full  = total_cost(list(AGENT_COSTS.keys()))
        cheap = total_cost(TIER_AGENTS[Tier.CHEAP])
        assert full > cheap

    def test_single_agent_matches_cost_score(self):
        assert abs(total_cost(["api_agent"]) - AGENT_COSTS["api_agent"].cost_score) < 1e-9


# ── estimated_duration_s() ────────────────────────────────────────────────────

class TestEstimatedDuration:
    def test_empty_list_zero(self):
        assert estimated_duration_s([]) == 0.0

    def test_cheap_agents_parallel_time_is_max_not_sum(self):
        cheap = TIER_AGENTS[Tier.CHEAP]
        max_duration = max(AGENT_COSTS[a].avg_duration_s for a in cheap)
        result = estimated_duration_s(cheap)
        # Should equal the max (parallel), not the sum
        total_sum = sum(AGENT_COSTS[a].avg_duration_s for a in cheap)
        assert abs(result - max_duration) < 1e-9, "Cheap agents should run in parallel (max time)"
        assert result < total_sum, "Parallel should be less than serial sum"

    def test_full_tier_adds_serial_time_for_full_agents(self):
        all_agents = agents_for_tier(Tier.FULL)
        full_only  = TIER_AGENTS[Tier.FULL]
        serial_sum = sum(AGENT_COSTS[a].avg_duration_s for a in full_only)
        result = estimated_duration_s(all_agents)
        assert result >= serial_sum, "Full-tier agents must run serially (sum time)"

    def test_single_cheap_agent_returns_its_duration(self):
        result = estimated_duration_s(["api_agent"])
        assert abs(result - AGENT_COSTS["api_agent"].avg_duration_s) < 1e-9


# ── carbon_cost_grams() ───────────────────────────────────────────────────────

class TestCarbonCostGrams:
    def test_zero_intensity_zero_cost(self):
        agents = TIER_AGENTS[Tier.CHEAP]
        assert carbon_cost_grams(agents, carbon_intensity=0.0) == 0.0

    def test_higher_intensity_higher_cost(self):
        agents = agents_for_tier(Tier.FULL)
        low  = carbon_cost_grams(agents, carbon_intensity=100.0)
        high = carbon_cost_grams(agents, carbon_intensity=600.0)
        assert high > low

    def test_formula_correctness(self):
        """Verify the formula: energy_kWh = (duration_h × TDP × PUE) / 1000; co2 = energy × intensity"""
        agents    = ["api_agent"]
        intensity = 400.0
        pue       = 1.4
        tdp       = 45.0
        dur_h     = AGENT_COSTS["api_agent"].avg_duration_s / 3600
        expected  = ((dur_h * tdp * pue) / 1000) * intensity
        result    = carbon_cost_grams(agents, intensity, pue=pue, tdp_watts=tdp)
        assert abs(result - expected) < 1e-9

    def test_empty_agents_zero_cost(self):
        assert carbon_cost_grams([], carbon_intensity=400.0) == 0.0

    def test_full_suite_vs_cheap_only_savings(self):
        full_cost  = carbon_cost_grams(agents_for_tier(Tier.FULL), 500.0)
        cheap_cost = carbon_cost_grams(TIER_AGENTS[Tier.CHEAP], 500.0)
        assert full_cost > cheap_cost, "Full suite should cost more carbon than cheap-only"
