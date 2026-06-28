"""
conftest.py
===========
Shared pytest fixtures for all test phases of the carbon-aware CI scheduler.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheduler.carbon_client import CarbonForecast, CarbonWindow


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_window(
    intensity: float,
    hours_ahead: float = 2.0,
    zone: str = "eastus",
    is_optimal: bool = False,
) -> CarbonWindow:
    now = datetime.now(timezone.utc)
    return CarbonWindow(
        zone=zone,
        start=now + timedelta(hours=hours_ahead),
        end=now + timedelta(hours=hours_ahead + 0.5),
        intensity=intensity,
        is_optimal=is_optimal,
    )


def make_forecast(
    intensity: float = 300.0,
    threshold: float = 400.0,
    optimal_intensity: float = 150.0,
    hours_ahead: float = 2.0,
    zone: str = "eastus",
    windows: list[CarbonWindow] | None = None,
) -> CarbonForecast:
    optimal = make_window(optimal_intensity, hours_ahead, zone, is_optimal=True)
    return CarbonForecast(
        zone=zone,
        current_intensity=intensity,
        windows=windows if windows is not None else [optimal],
        optimal_window=optimal,
        is_high_carbon_now=(intensity >= threshold),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def low_carbon_forecast():
    """Grid is clean — no deferral should occur."""
    return make_forecast(intensity=200.0)


@pytest.fixture
def high_carbon_forecast():
    """Grid is dirty — deferral should occur for eligible tiers."""
    return make_forecast(intensity=500.0, optimal_intensity=100.0)


@pytest.fixture
def very_high_carbon_forecast():
    """Extreme carbon — maximum deferral savings scenario."""
    return make_forecast(intensity=700.0, optimal_intensity=50.0)


@pytest.fixture
def no_windows_forecast():
    """SDK returns no forecast windows — engine falls back to timeout."""
    return CarbonForecast(
        zone="eastus",
        current_intensity=450.0,
        windows=[],
        optimal_window=None,
        is_high_carbon_now=True,
    )


@pytest.fixture
def noop_run_fn():
    """Async no-op run function — records calls for assertion."""
    calls = []

    async def _fn(pr_id: str, agents: list, meta: dict = None):
        calls.append({"pr_id": pr_id, "agents": agents, "meta": meta})
        return {"status": "stub", "verdict": "PASS"}

    _fn.calls = calls
    return _fn


@pytest.fixture
def mock_carbon_client(high_carbon_forecast):
    """Mocked CarbonAwareClient that returns high_carbon_forecast by default."""
    client = AsyncMock()
    client.get_forecast = AsyncMock(return_value=high_carbon_forecast)
    client.current_intensity = AsyncMock(return_value=500.0)
    client.close = AsyncMock()
    return client
