"""
FastAPI app for the carbon-aware CI scheduler.

Run locally:
    uvicorn api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from scheduler.carbon_client import CARBON_SDK_BASE_URL, CARBON_SDK_ZONE
from scheduler.scheduler import CarbonAwareScheduler, PREvent


SERVICE_NAME = "carbon-aware-ci-scheduler"
API_TOKEN = os.getenv("SCHEDULER_API_TOKEN", "").strip()


class PRWebhookRequest(BaseModel):
    pr_id: str = Field(..., min_length=1)
    risk_score: float = Field(..., ge=0.0, le=1.0)
    zone: Optional[str] = None
    sla_deadline: Optional[datetime] = None
    force_full: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = CarbonAwareScheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.stop()
        await scheduler._carbon.close()


app = FastAPI(
    title="K11tech Carbon-Aware CI Scheduler",
    version="1.0.0",
    lifespan=lifespan,
)


def _scheduler() -> CarbonAwareScheduler:
    return app.state.scheduler


def _require_token(authorization: str | None = Header(default=None)) -> None:
    if not API_TOKEN:
        return
    expected = f"Bearer {API_TOKEN}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )


def _window_to_dict(window) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "zone": window.zone,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "intensity": window.intensity,
        "is_optimal": window.is_optimal,
    }


def _decision_to_dict(decision) -> dict[str, Any]:
    return {
        "pr_id": decision.pr_id,
        "risk_bucket": decision.risk_bucket.value,
        "carbon_intensity": decision.carbon_intensity,
        "immediate_agents": decision.immediate_agents,
        "deferred_agents": decision.deferred_agents,
        "deferred_window": _window_to_dict(decision.deferred_window),
        "has_deferred_jobs": decision.has_deferred_jobs,
        "estimated_savings_g_co2": decision.estimated_savings_g_co2,
        "deferral_reason": decision.deferral_reason,
        "decided_at": decision.decided_at.isoformat(),
    }


def _metrics_text(metrics: dict[str, int | float]) -> str:
    lines: list[str] = []
    for key, value in metrics.items():
        lines.append(f"# TYPE {key} gauge")
        lines.append(f"{key} {value}")
    return "\n".join(lines) + "\n"


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "time": datetime.now(timezone.utc).isoformat(),
        "carbon_sdk_base_url": CARBON_SDK_BASE_URL,
        "carbon_sdk_zone": CARBON_SDK_ZONE,
    }


@app.get("/metrics")
async def metrics(
    _: None = Depends(_require_token),
    scheduler: CarbonAwareScheduler = Depends(_scheduler),
) -> Response:
    return Response(
        content=_metrics_text(scheduler.metrics()),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.options("/webhook/pr", status_code=status.HTTP_204_NO_CONTENT)
async def webhook_options() -> Response:
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Allow": "OPTIONS, POST"},
    )


@app.post("/webhook/pr", status_code=status.HTTP_202_ACCEPTED)
async def submit_pr(
    payload: PRWebhookRequest,
    _: None = Depends(_require_token),
    scheduler: CarbonAwareScheduler = Depends(_scheduler),
) -> dict[str, Any]:
    decision = await scheduler.submit(
        PREvent(
            pr_id=payload.pr_id,
            risk_score=payload.risk_score,
            zone=payload.zone,
            sla_deadline=payload.sla_deadline,
            force_full=payload.force_full,
            metadata=payload.metadata,
        )
    )
    return {
        "status": "accepted",
        "decision": _decision_to_dict(decision),
    }
