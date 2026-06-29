# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
carbon_client.py
================
Carbon intensity client for the Green Software Foundation Carbon Aware SDK.

The Carbon Aware SDK exposes a REST API that normalises multiple carbon intensity
data sources (Electricity Maps, WattTime, etc.) behind a single interface.
By default it runs locally via the Carbon Aware WebAPI Docker image:

    docker run -p 8090:8090 ghcr.io/green-software-foundation/carbon-aware-sdk:latest

Environment variables (see config/settings.py):
    CARBON_SDK_BASE_URL   – base URL of the Carbon Aware WebAPI (default: http://localhost:8090)
    CARBON_SDK_ZONE       – default location/zone, e.g. "eastus", "IE", "DE" (default: "eastus")
    CARBON_HIGH_THRESHOLD – gCO2eq/kWh above which a window is considered "high carbon" (default: 400)
    CARBON_SEARCH_HOURS   – how many hours ahead to search for a low-carbon window (default: 6)

Docs: https://github.com/Green-Software-Foundation/carbon-aware-sdk
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CARBON_SDK_BASE_URL   = os.getenv("CARBON_SDK_BASE_URL",   "http://localhost:8090")
CARBON_SDK_ZONE       = os.getenv("CARBON_SDK_ZONE",       "eastus")
CARBON_HIGH_THRESHOLD = float(os.getenv("CARBON_HIGH_THRESHOLD", "400"))  # gCO2eq/kWh
CARBON_SEARCH_HOURS   = int(os.getenv("CARBON_SEARCH_HOURS", "6"))


@dataclass
class CarbonWindow:
    """A time window with its associated carbon intensity."""
    zone:          str
    start:         datetime
    end:           datetime
    intensity:     float       # gCO2eq/kWh
    is_optimal:    bool = False

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)

    def __str__(self) -> str:
        flag = " ✓ optimal" if self.is_optimal else ""
        return (
            f"[{self.start.strftime('%H:%M')}–{self.end.strftime('%H:%M')} UTC] "
            f"{self.intensity:.1f} gCO2eq/kWh{flag}"
        )


@dataclass
class CarbonForecast:
    """Complete carbon forecast for a zone over a search window."""
    zone:              str
    current_intensity: float
    windows:           list[CarbonWindow] = field(default_factory=list)
    optimal_window:    Optional[CarbonWindow] = None
    is_high_carbon_now: bool = False

    @property
    def can_defer_for_savings(self) -> bool:
        """True if deferring to the optimal window would reduce carbon intensity."""
        if not self.optimal_window:
            return False
        return self.optimal_window.intensity < self.current_intensity * 0.85  # >15% savings


class CarbonAwareClient:
    """
    Async client for the Green Software Foundation Carbon Aware WebAPI.

    Provides two main operations:
      1. current_intensity(zone)       – current gCO2eq/kWh for a grid zone
      2. best_window(zone, duration)   – lowest-carbon time window in the next N hours
    """

    def __init__(
        self,
        base_url: str = CARBON_SDK_BASE_URL,
        zone: str = CARBON_SDK_ZONE,
        high_threshold: float = CARBON_HIGH_THRESHOLD,
        search_hours: int = CARBON_SEARCH_HOURS,
        timeout: float = 10.0,
    ) -> None:
        self.base_url       = base_url.rstrip("/")
        self.zone           = zone
        self.high_threshold = high_threshold
        self.search_hours   = search_hours
        self._client        = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "CarbonAwareClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ── Public API ────────────────────────────────────────────────────────────

    async def current_intensity(self, zone: Optional[str] = None) -> float:
        """
        Return the current carbon intensity in gCO2eq/kWh for the given zone.
        Falls back to CARBON_HIGH_THRESHOLD + 1 if the SDK is unavailable,
        so jobs are conservatively deferred rather than silently scheduled in
        high-carbon windows.
        """
        z = zone or self.zone
        try:
            resp = await self._client.get(
                f"{self.base_url}/emissions/bylocations/best",
                params={"location": z},
            )
            resp.raise_for_status()
            data = resp.json()
            # SDK returns a list; pick the first (best) entry
            if isinstance(data, list) and data:
                return float(data[0].get("rating", self.high_threshold + 1))
            return self.high_threshold + 1
        except Exception as exc:
            logger.warning("Carbon Aware SDK unavailable (%s); using fallback intensity.", exc)
            return self.high_threshold + 1

    async def get_forecast(self, zone: Optional[str] = None) -> CarbonForecast:
        """
        Fetch a carbon intensity forecast for the next CARBON_SEARCH_HOURS hours
        and identify the optimal (lowest-carbon) execution window.
        """
        z      = zone or self.zone
        now    = datetime.now(timezone.utc)
        end_dt = now + timedelta(hours=self.search_hours)

        current = await self.current_intensity(z)
        windows: list[CarbonWindow] = []

        try:
            resp = await self._client.get(
                f"{self.base_url}/emissions/forecasts/current",
                params={
                    "location":  z,
                    "dataStartAt": now.isoformat(),
                    "dataEndAt":   end_dt.isoformat(),
                    "windowSize":  30,  # 30-minute windows
                },
            )
            resp.raise_for_status()
            forecasts = resp.json()

            # forecasts is a list of {location, optimalDataPoints: [{timestamp, value}]}
            for fc in (forecasts if isinstance(forecasts, list) else []):
                for pt in fc.get("optimalDataPoints", []):
                    ts    = datetime.fromisoformat(pt["timestamp"].replace("Z", "+00:00"))
                    value = float(pt["value"])
                    windows.append(CarbonWindow(
                        zone=z,
                        start=ts,
                        end=ts + timedelta(minutes=30),
                        intensity=value,
                    ))

        except Exception as exc:
            logger.warning("Forecast unavailable (%s); returning empty window list.", exc)

        # Find optimal (lowest intensity) window
        optimal = min(windows, key=lambda w: w.intensity) if windows else None
        if optimal:
            optimal.is_optimal = True

        forecast = CarbonForecast(
            zone=z,
            current_intensity=current,
            windows=windows,
            optimal_window=optimal,
            is_high_carbon_now=(current >= self.high_threshold),
        )
        logger.info(
            "Carbon forecast: zone=%s current=%.1f high=%s optimal=%s",
            z, current, forecast.is_high_carbon_now,
            optimal.start.isoformat() if optimal else "none",
        )
        return forecast

    async def best_window(
        self,
        zone: Optional[str] = None,
        duration_minutes: int = 30,
    ) -> Optional[CarbonWindow]:
        """
        Return the lowest-carbon window of at least `duration_minutes` in the
        next CARBON_SEARCH_HOURS hours, or None if no forecast is available.
        """
        forecast = await self.get_forecast(zone)
        # Filter windows that are long enough
        candidates = [
            w for w in forecast.windows
            if w.duration_minutes >= duration_minutes
        ]
        if not candidates:
            return forecast.optimal_window  # fallback to any optimal window
        best = min(candidates, key=lambda w: w.intensity)
        best.is_optimal = True
        return best

    async def is_low_carbon_now(self, zone: Optional[str] = None) -> bool:
        """True if the current intensity is below the configured threshold."""
        intensity = await self.current_intensity(zone)
        return intensity < self.high_threshold


# ── Convenience function ──────────────────────────────────────────────────────

async def get_current_carbon(zone: Optional[str] = None) -> float:
    """One-shot helper — creates a client, fetches intensity, closes the client."""
    async with CarbonAwareClient() as client:
        return await client.current_intensity(zone)


if __name__ == "__main__":
    async def _demo():
        async with CarbonAwareClient() as client:
            intensity = await client.current_intensity()
            print(f"Current intensity ({client.zone}): {intensity:.1f} gCO2eq/kWh")
            forecast  = await client.get_forecast()
            print(f"High carbon now: {forecast.is_high_carbon_now}")
            if forecast.optimal_window:
                print(f"Best window: {forecast.optimal_window}")
    asyncio.run(_demo())
