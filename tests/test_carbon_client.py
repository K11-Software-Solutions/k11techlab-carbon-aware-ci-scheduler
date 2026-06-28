"""
test_carbon_client.py
=====================
Phase 1 — Unit tests for CarbonAwareClient.

All HTTP calls are intercepted via respx (httpx mock library).
No real Carbon Aware SDK required.

Install: pip install respx --break-system-packages
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import respx
import httpx

from scheduler.carbon_client import (
    CarbonAwareClient,
    CarbonForecast,
    CarbonWindow,
    get_current_carbon,
)

BASE = "http://localhost:8090"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(hours_ahead: float = 2.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return dt.isoformat()


def _intensity_response(rating: float, zone: str = "eastus") -> list[dict]:
    return [{"location": zone, "rating": rating, "time": _ts(0)}]


def _forecast_response(points: list[tuple[float, float]], zone: str = "eastus") -> list[dict]:
    """points: list of (hours_ahead, intensity)"""
    return [{
        "location": zone,
        "optimalDataPoints": [
            {"timestamp": _ts(h), "value": v, "location": zone}
            for h, v in points
        ],
    }]


# ── current_intensity() ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCurrentIntensity:
    async def test_returns_sdk_rating(self):
        async with CarbonAwareClient(base_url=BASE, zone="eastus") as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(320.5))
                )
                result = await client.current_intensity()
        assert abs(result - 320.5) < 0.01

    async def test_sdk_unavailable_returns_fallback(self):
        async with CarbonAwareClient(base_url=BASE, high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    side_effect=httpx.ConnectError("refused")
                )
                result = await client.current_intensity()
        assert result > 400.0, "Fallback must exceed threshold (conservative)"

    async def test_sdk_returns_empty_list_falls_back(self):
        async with CarbonAwareClient(base_url=BASE, high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=[])
                )
                result = await client.current_intensity()
        assert result > 400.0

    async def test_zone_override(self):
        async with CarbonAwareClient(base_url=BASE, zone="eastus") as client:
            with respx.mock(base_url=BASE) as mock:
                req = mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(250.0, zone="IE"))
                )
                await client.current_intensity(zone="IE")
        assert req.called
        assert "IE" in str(req.calls[0].request.url)

    async def test_http_error_falls_back(self):
        async with CarbonAwareClient(base_url=BASE, high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(503)
                )
                result = await client.current_intensity()
        assert result > 400.0


# ── get_forecast() ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetForecast:
    async def test_forecast_high_carbon(self):
        """Current intensity above threshold → is_high_carbon_now=True."""
        async with CarbonAwareClient(base_url=BASE, zone="eastus", high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(500.0))
                )
                mock.get("/emissions/forecasts/current").mock(
                    return_value=httpx.Response(200, json=_forecast_response([
                        (1.0, 120.0), (2.0, 90.0), (3.0, 200.0)
                    ]))
                )
                forecast = await client.get_forecast()

        assert forecast.is_high_carbon_now is True
        assert forecast.current_intensity == pytest.approx(500.0)

    async def test_forecast_low_carbon(self):
        async with CarbonAwareClient(base_url=BASE, zone="eastus", high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(200.0))
                )
                mock.get("/emissions/forecasts/current").mock(
                    return_value=httpx.Response(200, json=_forecast_response([(1.0, 150.0)]))
                )
                forecast = await client.get_forecast()

        assert forecast.is_high_carbon_now is False

    async def test_optimal_window_is_lowest_intensity(self):
        async with CarbonAwareClient(base_url=BASE) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(500.0))
                )
                mock.get("/emissions/forecasts/current").mock(
                    return_value=httpx.Response(200, json=_forecast_response([
                        (1.0, 300.0), (2.0, 80.0), (3.0, 200.0)
                    ]))
                )
                forecast = await client.get_forecast()

        assert forecast.optimal_window is not None
        assert forecast.optimal_window.is_optimal is True
        assert forecast.optimal_window.intensity == pytest.approx(80.0)

    async def test_forecast_sdk_unavailable_empty_windows(self):
        async with CarbonAwareClient(base_url=BASE, high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(450.0))
                )
                mock.get("/emissions/forecasts/current").mock(
                    side_effect=httpx.ConnectError("refused")
                )
                forecast = await client.get_forecast()

        assert forecast.windows == []
        assert forecast.optimal_window is None
        assert forecast.is_high_carbon_now is True

    async def test_can_defer_for_savings_true_when_big_gap(self):
        async with CarbonAwareClient(base_url=BASE) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(600.0))
                )
                mock.get("/emissions/forecasts/current").mock(
                    return_value=httpx.Response(200, json=_forecast_response([(2.0, 50.0)]))
                )
                forecast = await client.get_forecast()

        assert forecast.can_defer_for_savings is True  # 50 < 600 * 0.85

    async def test_can_defer_for_savings_false_when_marginal(self):
        async with CarbonAwareClient(base_url=BASE) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(400.0))
                )
                mock.get("/emissions/forecasts/current").mock(
                    return_value=httpx.Response(200, json=_forecast_response([(2.0, 380.0)]))
                )
                forecast = await client.get_forecast()

        # 380 < 400 * 0.85 = 340? No — so can_defer_for_savings should be False
        assert forecast.can_defer_for_savings is False


# ── is_low_carbon_now() ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestIsLowCarbonNow:
    async def test_low_carbon(self):
        async with CarbonAwareClient(base_url=BASE, high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(200.0))
                )
                result = await client.is_low_carbon_now()
        assert result is True

    async def test_high_carbon(self):
        async with CarbonAwareClient(base_url=BASE, high_threshold=400.0) as client:
            with respx.mock(base_url=BASE) as mock:
                mock.get("/emissions/bylocations/best").mock(
                    return_value=httpx.Response(200, json=_intensity_response(500.0))
                )
                result = await client.is_low_carbon_now()
        assert result is False


# ── CarbonWindow dataclass ────────────────────────────────────────────────────

class TestCarbonWindow:
    def test_duration_minutes(self):
        now = datetime.now(timezone.utc)
        w = CarbonWindow(
            zone="IE",
            start=now,
            end=now + timedelta(minutes=30),
            intensity=100.0,
        )
        assert w.duration_minutes == 30

    def test_str_representation(self):
        now = datetime.now(timezone.utc)
        w = CarbonWindow(zone="IE", start=now, end=now + timedelta(minutes=30),
                         intensity=120.5, is_optimal=True)
        s = str(w)
        assert "120.5" in s
        assert "optimal" in s


# ── get_current_carbon() convenience ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_current_carbon_convenience():
    with respx.mock(base_url=BASE) as mock:
        mock.get("/emissions/bylocations/best").mock(
            return_value=httpx.Response(200, json=_intensity_response(310.0))
        )
        result = await get_current_carbon()
    assert abs(result - 310.0) < 0.01
