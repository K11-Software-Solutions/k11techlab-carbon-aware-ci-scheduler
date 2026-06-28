"""
cost_model.py
=============
Per-agent compute cost model for the K11tech QA pipeline.

Each agent is assigned a cost_score [0.0, 1.0] representing its relative
compute intensity (CPU time × memory × estimated duration). Agents are
grouped into tiers that the risk router uses to decide which agents to run
immediately vs. defer to a low-carbon window.

Tier definitions
----------------
CHEAP   – fast, stateless, low CPU. Always run immediately regardless of carbon.
MEDIUM  – moderate cost. Run immediately for medium/high-risk PRs; defer for low-risk.
FULL    – expensive (E2E, cross-browser, performance). Always defer to low-carbon
          window unless risk is CRITICAL (score >= 0.90) or SLA is imminent.

Cost scores are empirically calibrated from k11techlab pipeline telemetry:
  - Median wall-clock time per agent (seconds)
  - Peak memory usage (MB)
  - GPU utilisation (0 = none, 1.0 = full GPU)
Normalised to [0.0, 1.0] where 1.0 = most expensive agent in the suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Tier(str, Enum):
    CHEAP  = "cheap"
    MEDIUM = "medium"
    FULL   = "full"


@dataclass(frozen=True)
class AgentCost:
    name:            str
    tier:            Tier
    cost_score:      float        # [0.0, 1.0]
    avg_duration_s:  float        # median wall-clock seconds
    peak_memory_mb:  int          # peak resident memory
    description:     str = ""
    gpu:             bool = False  # True if the agent uses GPU acceleration


# ── Cost catalogue ────────────────────────────────────────────────────────────
# Sourced from 180-PR pipeline telemetry (Paper 5 dataset).
# cost_score = (0.5 × norm_duration + 0.3 × norm_memory + 0.2 × gpu_flag)

AGENT_COSTS: dict[str, AgentCost] = {
    # ── CHEAP tier ─────────────────────────────────────────────────────────────
    "api_agent": AgentCost(
        name="api_agent",
        tier=Tier.CHEAP,
        cost_score=0.18,
        avg_duration_s=4.2,
        peak_memory_mb=180,
        description="Static OpenAPI contract diff + Claude Haiku call",
    ),
    "security_agent": AgentCost(
        name="security_agent",
        tier=Tier.CHEAP,
        cost_score=0.22,
        avg_duration_s=5.1,
        peak_memory_mb=210,
        description="SAST pattern matching + LLM triage",
    ),
    "data_agent": AgentCost(
        name="data_agent",
        tier=Tier.CHEAP,
        cost_score=0.15,
        avg_duration_s=3.8,
        peak_memory_mb=160,
        description="Schema migration diff + data integrity checks",
    ),
    "a11y_agent": AgentCost(
        name="a11y_agent",
        tier=Tier.CHEAP,
        cost_score=0.20,
        avg_duration_s=4.7,
        peak_memory_mb=195,
        description="WCAG rule evaluation via axe-core (headless)",
    ),
    "regression_agent": AgentCost(
        name="regression_agent",
        tier=Tier.CHEAP,
        cost_score=0.19,
        avg_duration_s=4.5,
        peak_memory_mb=185,
        description="Pytest unit test suite (fast subset)",
    ),

    # ── MEDIUM tier ────────────────────────────────────────────────────────────
    "cross_repo_impact_agent": AgentCost(
        name="cross_repo_impact_agent",
        tier=Tier.MEDIUM,
        cost_score=0.38,
        avg_duration_s=9.4,
        peak_memory_mb=320,
        description="Contract Registry MCP queries + NetworkX graph traversal",
    ),
    "drift_analysis_agent": AgentCost(
        name="drift_analysis_agent",
        tier=Tier.MEDIUM,
        cost_score=0.35,
        avg_duration_s=8.7,
        peak_memory_mb=290,
        description="Contract change velocity computation (Paper 4 drift floor)",
    ),

    # ── FULL tier ──────────────────────────────────────────────────────────────
    "playwright_agent": AgentCost(
        name="playwright_agent",
        tier=Tier.FULL,
        cost_score=0.72,
        avg_duration_s=38.5,
        peak_memory_mb=680,
        description="Playwright E2E test suite (Chromium + Firefox + WebKit)",
    ),
    "perf_agent": AgentCost(
        name="perf_agent",
        tier=Tier.FULL,
        cost_score=0.85,
        avg_duration_s=52.3,
        peak_memory_mb=890,
        description="k6 load test + Lighthouse performance audit",
    ),
    "browser_agent": AgentCost(
        name="browser_agent",
        tier=Tier.FULL,
        cost_score=0.68,
        avg_duration_s=34.1,
        peak_memory_mb=620,
        description="Cross-browser visual regression (Chromium + Firefox)",
    ),
}


# ── Tier groupings ────────────────────────────────────────────────────────────

TIER_AGENTS: dict[Tier, list[str]] = {
    Tier.CHEAP:  [n for n, a in AGENT_COSTS.items() if a.tier == Tier.CHEAP],
    Tier.MEDIUM: [n for n, a in AGENT_COSTS.items() if a.tier == Tier.MEDIUM],
    Tier.FULL:   [n for n, a in AGENT_COSTS.items() if a.tier == Tier.FULL],
}


def agents_for_tier(tier: Tier) -> list[str]:
    """Return all agent names at or below the given tier."""
    tiers = [Tier.CHEAP]
    if tier in (Tier.MEDIUM, Tier.FULL):
        tiers.append(Tier.MEDIUM)
    if tier == Tier.FULL:
        tiers.append(Tier.FULL)
    return [name for t in tiers for name in TIER_AGENTS[t]]


def total_cost(agent_names: list[str]) -> float:
    """Sum of cost_scores for the given agent list."""
    return sum(AGENT_COSTS[n].cost_score for n in agent_names if n in AGENT_COSTS)


def estimated_duration_s(agent_names: list[str]) -> float:
    """
    Estimated wall-clock duration for the given agent set, assuming
    CHEAP/MEDIUM agents run in parallel and FULL agents run serially
    (resource contention).
    """
    cheap_medium = [n for n in agent_names
                    if n in AGENT_COSTS and AGENT_COSTS[n].tier != Tier.FULL]
    full_agents  = [n for n in agent_names
                    if n in AGENT_COSTS and AGENT_COSTS[n].tier == Tier.FULL]

    # Parallel: wall time = max of concurrent agents
    parallel_time = max((AGENT_COSTS[n].avg_duration_s for n in cheap_medium), default=0.0)
    # Serial: sum of full-tier agents (memory pressure prevents full parallelism)
    serial_time   = sum(AGENT_COSTS[n].avg_duration_s for n in full_agents)

    return parallel_time + serial_time


def carbon_cost_grams(
    agent_names: list[str],
    carbon_intensity: float,
    pue: float = 1.4,
    tdp_watts: float = 45.0,
) -> float:
    """
    Estimate CO2 emissions in grams for running the given agent set.

    Formula:
        energy_kWh = (duration_hours × tdp_watts × pue) / 1000
        carbon_g   = energy_kWh × carbon_intensity (gCO2eq/kWh)

    Args:
        agent_names:       agents to run
        carbon_intensity:  current grid intensity in gCO2eq/kWh
        pue:               Power Usage Effectiveness of the data centre (default 1.4)
        tdp_watts:         thermal design power per agent slot (default 45W — typical CI runner)
    """
    duration_h  = estimated_duration_s(agent_names) / 3600
    energy_kwh  = (duration_h * tdp_watts * pue) / 1000
    return energy_kwh * carbon_intensity


if __name__ == "__main__":
    full_suite = list(AGENT_COSTS.keys())
    cheap_only = agents_for_tier(Tier.CHEAP)

    print("=== Agent Cost Model ===")
    for tier in Tier:
        agents = TIER_AGENTS[tier]
        print(f"\n{tier.value.upper()} tier:")
        for name in agents:
            a = AGENT_COSTS[name]
            print(f"  {name:<30} cost={a.cost_score:.2f}  ~{a.avg_duration_s:.0f}s  {a.peak_memory_mb}MB")

    print(f"\nFull suite total cost:  {total_cost(full_suite):.2f}")
    print(f"Cheap-only total cost:  {total_cost(cheap_only):.2f}")
    print(f"Full suite est. duration:  {estimated_duration_s(full_suite):.1f}s")
    print(f"Cheap-only est. duration:  {estimated_duration_s(cheap_only):.1f}s")
    print(f"\nCarbon @ 400 gCO2/kWh — full suite: "
          f"{carbon_cost_grams(full_suite, 400):.2f}g CO2")
    print(f"Carbon @ 100 gCO2/kWh — full suite: "
          f"{carbon_cost_grams(full_suite, 100):.2f}g CO2")
