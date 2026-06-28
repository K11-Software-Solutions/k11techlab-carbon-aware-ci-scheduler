"""
risk_router.py
==============
Routes a pull request to the appropriate agent tier based on its QA pipeline
risk score and the current carbon intensity of the grid.

Routing logic
-------------
Risk buckets (from K11tech QA pipeline risk_score [0.0, 1.0]):

    LOW      (< 0.40):  Run CHEAP tier immediately.
                        Defer MEDIUM agents to low-carbon window.
                        Skip FULL tier entirely (PR does not warrant it).

    MEDIUM   (0.40-0.69): Run CHEAP immediately.
                           Defer MEDIUM agents to low-carbon window if carbon is high.
                           Skip FULL tier.

    HIGH     (0.70-0.89): Run CHEAP + MEDIUM + playwright immediately.
                           Defer perf_agent and browser_agent to low-carbon window
                           if carbon is high and SLA allows.

    CRITICAL (>= 0.90): Run everything immediately.
                         No deferral regardless of carbon. Safety > sustainability.

Carbon gate
-----------
If grid intensity < CARBON_HIGH_THRESHOLD the window is "low carbon" and no
deferral occurs regardless of risk bucket.

SLA guard
---------
Deferral is capped at the SLA deadline. If the optimal window falls after the
deadline, the earliest window before the deadline is used instead.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .carbon_client import CarbonForecast, CarbonWindow
from .cost_model import TIER_AGENTS, Tier, agents_for_tier, carbon_cost_grams

logger = logging.getLogger(__name__)

CARBON_HIGH_THRESHOLD = float(os.getenv("CARBON_HIGH_THRESHOLD", "400"))


class RiskBucket(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


def classify_risk(risk_score: float) -> RiskBucket:
    if risk_score < 0.40:
        return RiskBucket.LOW
    if risk_score < 0.70:
        return RiskBucket.MEDIUM
    if risk_score < 0.90:
        return RiskBucket.HIGH
    return RiskBucket.CRITICAL


@dataclass
class RoutingDecision:
    """Result of routing a PR through the carbon-aware scheduler."""
    immediate_agents:         list[str]
    deferred_agents:          list[str]
    deferred_window:          Optional[CarbonWindow]
    risk_bucket:              RiskBucket
    carbon_intensity:         float
    deferral_reason:          str
    estimated_savings_g_co2:  float = 0.0
    pr_id:                    str = ""
    decided_at:               datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def has_deferred_jobs(self) -> bool:
        return bool(self.deferred_agents)

    def summary(self) -> str:
        lines = [
            f"PR: {self.pr_id or '(unknown)'}",
            f"Risk bucket:     {self.risk_bucket.value.upper()}",
            f"Carbon now:      {self.carbon_intensity:.1f} gCO2eq/kWh",
            f"Immediate agents ({len(self.immediate_agents)}): "
            f"{', '.join(self.immediate_agents)}",
        ]
        if self.deferred_agents:
            win = str(self.deferred_window) if self.deferred_window else "next available"
            lines += [
                f"Deferred agents  ({len(self.deferred_agents)}): "
                f"{', '.join(self.deferred_agents)}",
                f"Deferred window: {win}",
                f"Est. CO2 saved:  {self.estimated_savings_g_co2:.2f} g",
            ]
        lines.append(f"Reason: {self.deferral_reason}")
        return "\n".join(lines)


# Agents deferred even for HIGH-risk PRs when carbon is high.
# playwright stays immediate for functional coverage; perf+browser can wait.
_HIGH_RISK_DEFERRABLE = frozenset({"perf_agent", "browser_agent"})


class RiskRouter:
    """
    Routes a PR to an agent tier and decides whether to defer expensive agents
    based on the current carbon forecast.

    Usage:
        router   = RiskRouter()
        forecast = await carbon_client.get_forecast()
        decision = router.route(risk_score=0.55, forecast=forecast, pr_id="PR-42")
    """

    def route(
        self,
        risk_score:   float,
        forecast:     CarbonForecast,
        pr_id:        str = "",
        sla_deadline: Optional[datetime] = None,
        force_full:   bool = False,
    ) -> RoutingDecision:
        bucket    = classify_risk(risk_score)
        intensity = forecast.current_intensity

        logger.info(
            "Routing PR %s: risk=%.2f bucket=%s carbon=%.1f",
            pr_id, risk_score, bucket.value, intensity,
        )

        # CRITICAL or forced: run everything now, no deferral.
        if bucket == RiskBucket.CRITICAL or force_full:
            return RoutingDecision(
                immediate_agents=agents_for_tier(Tier.FULL),
                deferred_agents=[],
                deferred_window=None,
                risk_bucket=bucket,
                carbon_intensity=intensity,
                pr_id=pr_id,
                deferral_reason=(
                    "force_full override"
                    if force_full
                    else "CRITICAL risk — safety overrides carbon sustainability"
                ),
            )

        # Carbon is already low: no deferral needed.
        if not forecast.is_high_carbon_now:
            if bucket == RiskBucket.LOW:
                immediate = list(TIER_AGENTS[Tier.CHEAP])
                reason    = (
                    f"LOW risk — cheap tier only "
                    f"(carbon {intensity:.0f} gCO2/kWh is low, no deferral)"
                )
            elif bucket == RiskBucket.MEDIUM:
                immediate = agents_for_tier(Tier.MEDIUM)  # cheap + medium
                reason    = (
                    f"MEDIUM risk — cheap+medium tier "
                    f"(carbon {intensity:.0f} gCO2/kWh is low, no deferral)"
                )
            else:  # HIGH
                immediate = agents_for_tier(Tier.FULL)
                reason    = (
                    f"HIGH risk — full suite now "
                    f"(carbon {intensity:.0f} gCO2/kWh is low)"
                )
            return RoutingDecision(
                immediate_agents=immediate,
                deferred_agents=[],
                deferred_window=None,
                risk_bucket=bucket,
                carbon_intensity=intensity,
                pr_id=pr_id,
                deferral_reason=reason,
            )

        # Carbon is HIGH: defer what we can.
        optimal_window = self._pick_window(forecast, sla_deadline)

        if bucket == RiskBucket.LOW:
            # Run cheap now; defer medium-tier agents; skip FULL entirely.
            immediate = list(TIER_AGENTS[Tier.CHEAP])
            deferred  = list(TIER_AGENTS[Tier.MEDIUM])
            reason    = (
                f"LOW risk + high carbon ({intensity:.0f} gCO2/kWh) "
                f"— deferring medium agents to {optimal_window}"
            )

        elif bucket == RiskBucket.MEDIUM:
            # Run cheap now; defer medium-tier agents; skip FULL entirely.
            immediate = list(TIER_AGENTS[Tier.CHEAP])
            deferred  = list(TIER_AGENTS[Tier.MEDIUM])
            reason    = (
                f"MEDIUM risk + high carbon ({intensity:.0f} gCO2/kWh) "
                f"— deferring medium-tier agents"
            )

        else:  # HIGH
            # Run cheap + medium + playwright now; defer perf + browser.
            immediate = [
                a for a in agents_for_tier(Tier.FULL)
                if a not in _HIGH_RISK_DEFERRABLE
            ]
            deferred = [
                a for a in agents_for_tier(Tier.FULL)
                if a in _HIGH_RISK_DEFERRABLE
            ]
            reason = (
                f"HIGH risk + high carbon ({intensity:.0f} gCO2/kWh) "
                f"— deferring {', '.join(sorted(deferred))} to low-carbon window"
            )

        savings = _co2_savings(deferred, intensity, optimal_window)

        return RoutingDecision(
            immediate_agents=immediate,
            deferred_agents=deferred,
            deferred_window=optimal_window,
            risk_bucket=bucket,
            carbon_intensity=intensity,
            pr_id=pr_id,
            deferral_reason=reason,
            estimated_savings_g_co2=savings,
        )

    @staticmethod
    def _pick_window(
        forecast:     CarbonForecast,
        sla_deadline: Optional[datetime],
    ) -> Optional[CarbonWindow]:
        if not forecast.windows:
            return forecast.optimal_window
        candidates = (
            [w for w in forecast.windows if w.start < sla_deadline]
            if sla_deadline else forecast.windows
        )
        return min(candidates, key=lambda w: w.intensity) if candidates else None


def _co2_savings(
    deferred:       list[str],
    current_gco2:   float,
    optimal_window: Optional[CarbonWindow],
) -> float:
    if not deferred or not optimal_window:
        return 0.0
    cost_now      = carbon_cost_grams(deferred, current_gco2)
    cost_deferred = carbon_cost_grams(deferred, optimal_window.intensity)
    return max(0.0, cost_now - cost_deferred)
