# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
qa_pipeline_adapter.py
======================
Thin adapter between the carbon-aware scheduler and the K11tech QA pipeline.

The adapter calls runner.run_pipeline() from the QA system's runner.py with
an agent_tier parameter. The QA pipeline itself requires no changes — the
adapter translates the scheduler's agent list into the tier enum that
run_pipeline() already understands.

QA pipeline contract
--------------------
The QA pipeline (k11techlab-agentic-ai-qa-system) exposes:

    # runner.py
    async def run_pipeline(
        pr_id:      str,
        agent_tier: Literal["cheap", "medium", "full"],
        agents:     list[str] | None = None,   # optional: override tier with explicit list
        metadata:   dict | None = None,
    ) -> PipelineResult

If the QA pipeline is not installed, this adapter falls back to a stub that
logs what would have been called. This allows the scheduler to run
independently in development/testing environments.

Environment variables
---------------------
    QA_PIPELINE_MODULE   – Python module path to runner.py (default: k11techlab.runner)
    QA_PIPELINE_TIMEOUT  – seconds to wait for run_pipeline() to return (default: 600)
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

QA_PIPELINE_MODULE  = os.getenv("QA_PIPELINE_MODULE",  "k11techlab.runner")
QA_PIPELINE_TIMEOUT = int(os.getenv("QA_PIPELINE_TIMEOUT", "600"))   # 10 min default


@dataclass
class PipelineResult:
    """Normalised result from run_pipeline(). Maps both real and stub results."""
    pr_id:       str
    agents_run:  list[str]
    status:      str           # "passed" | "failed" | "hitl_escalated" | "error" | "stub"
    verdict:     str           # "PASS" | "FAIL" | "HITL"
    duration_s:  float
    started_at:  datetime
    metadata:    dict


class QAPipelineAdapter:
    """
    Adapter that calls the K11tech QA pipeline's run_pipeline() function.

    Lazy-imports the QA runner at first call so the scheduler can start
    even when the QA package is not installed.
    """

    def __init__(self) -> None:
        self._runner_module = None   # lazy-loaded
        self._is_stub       = False

    def _load_runner(self) -> None:
        if self._runner_module is not None:
            return
        try:
            self._runner_module = importlib.import_module(QA_PIPELINE_MODULE)
            logger.info("Loaded QA pipeline runner from %s", QA_PIPELINE_MODULE)
        except ImportError:
            logger.warning(
                "QA pipeline module '%s' not found — using stub adapter. "
                "Install k11techlab-agentic-ai-qa-system to enable real pipeline calls.",
                QA_PIPELINE_MODULE,
            )
            self._is_stub = True

    async def run_agents(
        self,
        pr_id:  str,
        agents: list[str],
        meta:   Optional[dict] = None,
    ) -> PipelineResult:
        """
        Run a specific set of agents for a PR.

        Called by DeferEngine when an immediate or deferred job fires.
        Translates the agent list to the appropriate tier string for run_pipeline().
        """
        self._load_runner()
        started_at = datetime.now(timezone.utc)
        meta       = meta or {}

        # Determine the agent_tier from the agent list
        tier = _agents_to_tier(agents)

        if self._is_stub:
            return await self._stub_run(pr_id, agents, tier, started_at, meta)

        try:
            raw_result = await asyncio.wait_for(
                self._runner_module.run_pipeline(
                    pr_id=pr_id,
                    agent_tier=tier,
                    agents=agents,        # explicit list overrides tier if supported
                    metadata=meta,
                ),
                timeout=QA_PIPELINE_TIMEOUT,
            )
            return _normalise_result(raw_result, pr_id, agents, started_at)

        except asyncio.TimeoutError:
            logger.error("run_pipeline timed out after %ds for PR %s", QA_PIPELINE_TIMEOUT, pr_id)
            return PipelineResult(
                pr_id=pr_id, agents_run=agents, status="error",
                verdict="FAIL", duration_s=QA_PIPELINE_TIMEOUT,
                started_at=started_at, metadata={"error": "timeout"},
            )
        except Exception as exc:
            logger.exception("run_pipeline raised for PR %s: %s", pr_id, exc)
            return PipelineResult(
                pr_id=pr_id, agents_run=agents, status="error",
                verdict="FAIL",
                duration_s=(datetime.now(timezone.utc) - started_at).total_seconds(),
                started_at=started_at, metadata={"error": str(exc)},
            )

    async def _stub_run(
        self,
        pr_id:      str,
        agents:     list[str],
        tier:       str,
        started_at: datetime,
        meta:       dict,
    ) -> PipelineResult:
        """
        Stub run: simulates the pipeline call for development/testing.
        Sleeps briefly to simulate work, then returns a synthetic PASS result.
        """
        logger.info(
            "[STUB] run_pipeline(pr_id=%s, agent_tier=%s, agents=%s)",
            pr_id, tier, agents,
        )
        await asyncio.sleep(0.5)  # simulate minimal work
        return PipelineResult(
            pr_id=pr_id,
            agents_run=agents,
            status="stub",
            verdict="PASS",
            duration_s=0.5,
            started_at=started_at,
            metadata={"tier": tier, "stub": True, **meta},
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _agents_to_tier(agents: list[str]) -> str:
    """Determine the highest tier present in the agent list."""
    from scheduler.cost_model import AGENT_COSTS, Tier
    tiers = {AGENT_COSTS[a].tier for a in agents if a in AGENT_COSTS}
    if Tier.FULL in tiers:
        return "full"
    if Tier.MEDIUM in tiers:
        return "medium"
    return "cheap"


def _normalise_result(
    raw,
    pr_id:      str,
    agents:     list[str],
    started_at: datetime,
) -> PipelineResult:
    """
    Normalise whatever run_pipeline() returns into a PipelineResult.
    Handles both dataclass and dict return types.
    """
    if isinstance(raw, dict):
        return PipelineResult(
            pr_id=pr_id,
            agents_run=agents,
            status=raw.get("status", "unknown"),
            verdict=raw.get("verdict", "UNKNOWN"),
            duration_s=raw.get("duration_s",
                       (datetime.now(timezone.utc) - started_at).total_seconds()),
            started_at=started_at,
            metadata=raw,
        )
    # Assume dataclass / object with attributes
    return PipelineResult(
        pr_id=pr_id,
        agents_run=agents,
        status=getattr(raw, "status", "unknown"),
        verdict=getattr(raw, "verdict", "UNKNOWN"),
        duration_s=getattr(raw, "duration_s",
                   (datetime.now(timezone.utc) - started_at).total_seconds()),
        started_at=started_at,
        metadata=getattr(raw, "metadata", {}),
    )
