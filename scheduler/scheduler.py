"""
scheduler.py
============
Main entrypoint for the K11tech Carbon-Aware CI Scheduler.

This module ties together:
  - CarbonAwareClient  (carbon_client.py)  — fetches grid intensity / forecast
  - RiskRouter         (risk_router.py)    — routes PR to agent tier + deferral decision
  - DeferEngine        (defer_engine.py)   — schedules immediate + deferred APScheduler jobs
  - QAPipelineAdapter  (integrations/)     — calls the QA pipeline's run_pipeline()

Entrypoints
-----------
  CarbonAwareScheduler.submit(pr)          — called from CI webhook (GitHub Actions, etc.)
  CarbonAwareScheduler.cancel_pr(pr_id)    — called when a PR is closed / superseded
  CarbonAwareScheduler.metrics()           — Prometheus-style counters for observability

Webhook usage (GitHub Actions example)
---------------------------------------
    POST /webhook/pr  { "pr_id": "42", "risk_score": 0.65, "zone": "IE" }
    → scheduler.submit(pr=PREvent(pr_id="42", risk_score=0.65, zone="IE"))

CLI usage
---------
    python -m scheduler.scheduler submit --pr-id PR-42 --risk-score 0.65
    python -m scheduler.scheduler metrics
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .carbon_client import CarbonAwareClient
from .defer_engine import DeferEngine
from .risk_router import RoutingDecision, RiskRouter

# We import the adapter lazily to avoid import errors if the QA repo isn't
# installed in this environment.
try:
    from integrations.qa_pipeline_adapter import QAPipelineAdapter
    _ADAPTER_AVAILABLE = True
except ImportError:
    _ADAPTER_AVAILABLE = False

logger = logging.getLogger(__name__)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


@dataclass
class PREvent:
    """Represents a PR arriving at the scheduler from a CI webhook."""
    pr_id:        str
    risk_score:   float
    zone:         Optional[str]   = None   # override default carbon zone
    sla_deadline: Optional[datetime] = None
    force_full:   bool            = False  # bypass carbon deferral
    metadata:     dict            = field(default_factory=dict)


@dataclass
class SchedulerMetrics:
    total_prs_submitted:   int   = 0
    total_immediate_jobs:  int   = 0
    total_deferred_jobs:   int   = 0
    total_co2_saved_g:     float = 0.0
    total_prs_critical:    int   = 0
    total_prs_low:         int   = 0


class CarbonAwareScheduler:
    """
    Top-level scheduler object. Instantiate once at application startup;
    use submit() for each incoming PR event.
    """

    def __init__(
        self,
        carbon_client: Optional[CarbonAwareClient] = None,
        risk_router:   Optional[RiskRouter]        = None,
        run_fn=None,
    ) -> None:
        """
        Args:
            carbon_client: optional pre-constructed CarbonAwareClient (useful for testing)
            risk_router:   optional pre-constructed RiskRouter (useful for testing)
            run_fn:        callable(pr_id, agents, meta) invoked by the DeferEngine.
                           Defaults to QAPipelineAdapter.run_agents if available.
        """
        self._carbon = carbon_client or CarbonAwareClient()
        self._router = risk_router   or RiskRouter()

        if run_fn is None:
            if _ADAPTER_AVAILABLE:
                self._adapter = QAPipelineAdapter()
                run_fn = self._adapter.run_agents
            else:
                run_fn = _noop_run_fn
                logger.warning(
                    "QAPipelineAdapter not found — using no-op run function. "
                    "Install the K11tech QA pipeline or provide a custom run_fn."
                )

        self._engine  = DeferEngine(run_fn=run_fn)
        self._metrics = SchedulerMetrics()

    def start(self) -> None:
        """Start the background scheduler. Call once at application startup."""
        self._engine.start()
        logger.info("CarbonAwareScheduler started")

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        self._engine.stop()
        logger.info("CarbonAwareScheduler stopped")

    async def submit(self, pr: PREvent) -> RoutingDecision:
        """
        Main entry point. Fetch the carbon forecast, route the PR, and schedule
        immediate + deferred jobs.

        Returns the RoutingDecision for the caller's observability / logging needs.
        """
        self._metrics.total_prs_submitted += 1
        logger.info("Received PR %s (risk=%.2f zone=%s)", pr.pr_id, pr.risk_score, pr.zone)

        # 1. Fetch carbon forecast
        forecast = await self._carbon.get_forecast(zone=pr.zone)

        # 2. Route
        decision = self._router.route(
            risk_score=pr.risk_score,
            forecast=forecast,
            pr_id=pr.pr_id,
            sla_deadline=pr.sla_deadline,
            force_full=pr.force_full,
        )

        logger.info("Routing decision for PR %s:\n%s", pr.pr_id, decision.summary())

        # 3. Schedule immediate jobs
        if decision.immediate_agents:
            self._engine.schedule_immediate(
                pr_id=pr.pr_id,
                agents=decision.immediate_agents,
                meta={
                    "risk_score":     pr.risk_score,
                    "risk_bucket":    decision.risk_bucket.value,
                    "carbon_intensity": decision.carbon_intensity,
                    **pr.metadata,
                },
            )
            self._metrics.total_immediate_jobs += 1

        # 4. Schedule deferred jobs
        if decision.has_deferred_jobs and decision.deferred_window:
            self._engine.schedule_deferred(
                pr_id=pr.pr_id,
                agents=decision.deferred_agents,
                run_at=decision.deferred_window.start,
                carbon_g_saved=decision.estimated_savings_g_co2,
                meta={
                    "risk_score":     pr.risk_score,
                    "deferred_window": str(decision.deferred_window),
                    **pr.metadata,
                },
            )
            self._metrics.total_deferred_jobs += 1

        # 5. Update metrics
        self._metrics.total_co2_saved_g += decision.estimated_savings_g_co2
        from .risk_router import RiskBucket
        if decision.risk_bucket == RiskBucket.CRITICAL:
            self._metrics.total_prs_critical += 1
        elif decision.risk_bucket == RiskBucket.LOW:
            self._metrics.total_prs_low += 1

        return decision

    def cancel_pr(self, pr_id: str) -> int:
        """Cancel all pending jobs for a PR. Returns count of jobs cancelled."""
        return self._engine.cancel_pr(pr_id)

    def metrics(self) -> dict:
        """Return Prometheus-style counters for observability."""
        engine_co2 = self._engine.total_co2_saved_g()
        return {
            "carbon_scheduler_prs_submitted_total":   self._metrics.total_prs_submitted,
            "carbon_scheduler_immediate_jobs_total":  self._metrics.total_immediate_jobs,
            "carbon_scheduler_deferred_jobs_total":   self._metrics.total_deferred_jobs,
            "carbon_scheduler_co2_saved_grams_total": engine_co2,
            "carbon_scheduler_jobs_pending":          len([
                j for j in self._engine.status()
                if not j["fired"] and not j["cancelled"]
            ]),
        }

    def job_status(self) -> list[dict]:
        """Return status of all known jobs."""
        return self._engine.status()

    async def __aenter__(self) -> "CarbonAwareScheduler":
        self.start()
        return self

    async def __aexit__(self, *_) -> None:
        self.stop()
        await self._carbon.close()


# ── No-op run function (when QA adapter is not available) ────────────────────

async def _noop_run_fn(pr_id: str, agents: list[str], meta: dict) -> dict:
    logger.info("[NOOP] Would run agents %s for PR %s", agents, pr_id)
    return {"pr_id": pr_id, "agents": agents, "status": "noop"}


# ── CLI ───────────────────────────────────────────────────────────────────────

async def _cli_submit(args: argparse.Namespace) -> None:
    async with CarbonAwareScheduler() as sched:
        pr = PREvent(
            pr_id=args.pr_id,
            risk_score=args.risk_score,
            zone=args.zone,
            force_full=args.force_full,
        )
        decision = await sched.submit(pr)
        print("\n" + decision.summary())
        if args.json:
            print("\n" + json.dumps({
                "risk_bucket":       decision.risk_bucket.value,
                "immediate_agents":  decision.immediate_agents,
                "deferred_agents":   decision.deferred_agents,
                "carbon_intensity":  decision.carbon_intensity,
                "co2_saved_g":       decision.estimated_savings_g_co2,
            }, indent=2))
        # Keep alive long enough for the immediate job to fire
        if not args.dry_run:
            await asyncio.sleep(5)


async def _cli_metrics(args: argparse.Namespace) -> None:
    async with CarbonAwareScheduler() as sched:
        m = sched.metrics()
        print(json.dumps(m, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="K11tech Carbon-Aware CI Scheduler")
    sub    = parser.add_subparsers(dest="command")

    # submit sub-command
    p_submit = sub.add_parser("submit", help="Submit a PR for carbon-aware scheduling")
    p_submit.add_argument("--pr-id",      required=True, help="Pull request ID")
    p_submit.add_argument("--risk-score", required=True, type=float, help="QA risk score [0.0–1.0]")
    p_submit.add_argument("--zone",       default=None,  help="Grid zone (e.g. IE, DE, eastus)")
    p_submit.add_argument("--force-full", action="store_true", help="Skip carbon deferral")
    p_submit.add_argument("--dry-run",    action="store_true", help="Don't wait for jobs to fire")
    p_submit.add_argument("--json",       action="store_true", help="Output JSON decision")

    # metrics sub-command
    sub.add_parser("metrics", help="Print current scheduler metrics")

    args = parser.parse_args()
    if args.command == "submit":
        asyncio.run(_cli_submit(args))
    elif args.command == "metrics":
        asyncio.run(_cli_metrics(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
