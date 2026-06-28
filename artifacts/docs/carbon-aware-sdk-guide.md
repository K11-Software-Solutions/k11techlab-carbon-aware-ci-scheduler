# Carbon Aware SDK — A Software Engineering Guide

> How to integrate real-time grid carbon data into your software systems using the Green Software Foundation's Carbon Aware SDK.

---

## What Is the Carbon Aware SDK?

The [Carbon Aware SDK](https://github.com/Green-Software-Foundation/carbon-aware-sdk) is an open-source toolkit published by the **Green Software Foundation (GSF)**. It provides a unified API over multiple electricity grid data providers, giving software systems access to real-time and forecast **carbon intensity** — the grams of CO₂ emitted per kilowatt-hour of electricity consumed in a given grid region.

Its core value proposition: **abstract away the data provider** so your application logic only talks to one interface, regardless of whether the underlying data comes from WattTime, Electricity Maps, or another source.

---

## Core Concepts

### Carbon Intensity
The number of grams of CO₂ equivalent emitted per kWh of electricity, for a given grid region at a given time.

```
gCO₂/kWh = carbon intensity
```

A wind-heavy grid might read **30 gCO₂/kWh**; a coal-heavy grid during peak demand might read **600 gCO₂/kWh**. The ratio between best and worst is often 10–20×.

### Marginal vs. Average Intensity
- **Average intensity**: the blended carbon cost of all electricity consumed right now.
- **Marginal intensity**: the carbon cost of the *next unit* of electricity consumed — i.e., what the grid operator will dispatch to meet an incremental load increase.

For workload scheduling, **marginal intensity** is more meaningful: it measures the actual impact of running your workload now vs. later.

### Grid Regions
Grid regions are defined by electricity balancing authorities (e.g., `eastus`, `uksouth`, `westeurope`). The SDK maps cloud regions to grid zones, so you can query by Azure/GCP/AWS region name.

### Carbon-Aware vs. Carbon-Neutral
- **Carbon-neutral**: offset emissions through credits (accounting manoeuvre).
- **Carbon-aware**: reduce actual emissions by timing or placing workloads intelligently (physical reduction).

The SDK enables the latter.

---

## Architecture Overview

```
Your Application
      │
      ▼
┌─────────────────────┐
│  Carbon Aware SDK   │  ◄── REST API, CLI, or SDK client
│                     │
│  WebAPI  │  CLI     │
└────┬────────────────┘
     │
     ▼
┌─────────────────────┐
│  Data Provider      │  ◄── WattTime / Electricity Maps / JSON mock
│  Abstraction Layer  │
└─────────────────────┘
```

The SDK ships as:
1. **WebAPI** — a self-hosted REST service (Docker image available).
2. **CLI** — command-line tool for scripting and shell integration.
3. **.NET library** — embeddable NuGet package for .NET applications.

Python, JavaScript, and other language integrations consume the **WebAPI** over HTTP.

---

## Getting the SDK Running

### Option 1: Docker (Recommended for CI Integration)

```bash
docker pull ghcr.io/green-software-foundation/carbon-aware-sdk-api:latest

docker run -d \
  -p 8090:80 \
  -e DataSources__ForecastDataSource=WattTime \
  -e DataSources__Configurations__WattTime__Username=<user> \
  -e DataSources__Configurations__WattTime__Password=<pass> \
  ghcr.io/green-software-foundation/carbon-aware-sdk-api:latest
```

### Option 2: JSON Mock (No API Key Needed — for Dev/Testing)

```bash
docker run -d \
  -p 8090:80 \
  -e DataSources__EmissionsDataSource=Json \
  -e DataSources__ForecastDataSource=Json \
  ghcr.io/green-software-foundation/carbon-aware-sdk-api:latest
```

The mock returns synthetic but structurally valid data — useful for local development and CI dry runs.

---

## REST API Reference

All endpoints are available at `http://localhost:8090` when running locally.

### Get Current Emissions

```http
GET /emissions/bylocation?location=eastus
```

**Response:**
```json
[
  {
    "location": "eastus",
    "time": "2026-06-24T10:00:00Z",
    "rating": 210.5,
    "duration": "00:05:00"
  }
]
```

### Get Best Execution Window (Forecast)

```http
GET /emissions/forecasts/best?
  location=eastus&
  dataStartAt=2026-06-24T10:00:00Z&
  dataEndAt=2026-06-24T18:00:00Z&
  windowSize=30
```

This is the most useful endpoint for scheduling. It returns the **lowest-carbon 30-minute window** within the specified time range.

**Response:**
```json
{
  "requestedAt": "2026-06-24T10:00:00Z",
  "location": "eastus",
  "dataStartAt": "2026-06-24T10:00:00Z",
  "dataEndAt": "2026-06-24T18:00:00Z",
  "windowSize": 30,
  "optimalDataPoints": [
    {
      "location": "eastus",
      "timestamp": "2026-06-24T14:30:00Z",
      "duration": 30,
      "value": 87.3
    }
  ]
}
```

### Compare Across Locations

```http
GET /emissions/bylocations/best?
  location=eastus&
  location=westeurope&
  location=australiaeast&
  time=2026-06-24T12:00:00Z
```

Returns the lowest-intensity region among those listed — useful for multi-region deployments that can shift workloads geographically.

---

## Python Integration

### Basic Carbon Client

```python
import httpx
from datetime import datetime, timedelta, timezone

CARBON_SDK_URL = "http://localhost:8090"

def get_current_intensity(location: str) -> float:
    resp = httpx.get(
        f"{CARBON_SDK_URL}/emissions/bylocation",
        params={"location": location},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0]["rating"] if data else 0.0


def get_best_window(
    location: str,
    window_minutes: int = 30,
    lookahead_hours: int = 8,
) -> dict:
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=lookahead_hours)
    resp = httpx.get(
        f"{CARBON_SDK_URL}/emissions/forecasts/best",
        params={
            "location": location,
            "dataStartAt": now.isoformat(),
            "dataEndAt": end.isoformat(),
            "windowSize": window_minutes,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
```

### Decision Helper

```python
CARBON_THRESHOLD_GRAMS = 200  # gCO₂/kWh

def should_run_now(location: str) -> tuple[bool, float]:
    intensity = get_current_intensity(location)
    return intensity <= CARBON_THRESHOLD_GRAMS, intensity


def get_deferred_start(location: str, window_minutes: int = 30) -> datetime | None:
    try:
        result = get_best_window(location, window_minutes)
        points = result.get("optimalDataPoints", [])
        if points:
            ts = points[0]["timestamp"]
            return datetime.fromisoformat(ts)
    except Exception:
        return None
    return None
```

---

## CLI Usage

The CLI is useful for shell scripts and GitHub Actions.

```bash
# Install
dotnet tool install -g GSF.CarbonAware.CLI

# Get current intensity
carbon-aware emissions -l eastus

# Get best window (outputs JSON)
carbon-aware emissions best-execution-time \
  --location eastus \
  --window-size 30 \
  --from $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --to $(date -u -d '+8 hours' +%Y-%m-%dT%H:%M:%SZ)
```

### GitHub Actions: Carbon Gate

```yaml
- name: Check carbon intensity
  id: carbon
  run: |
    INTENSITY=$(carbon-aware emissions -l eastus --format json | jq '.[0].rating')
    echo "intensity=$INTENSITY" >> $GITHUB_OUTPUT

- name: Skip expensive tests on high-carbon grid
  if: ${{ steps.carbon.outputs.intensity > 300 }}
  run: echo "Deferring E2E suite — grid intensity ${{ steps.carbon.outputs.intensity }} gCO₂/kWh"
```

---

## Data Providers

| Provider | Type | Key Required | Notes |
|----------|------|-------------|-------|
| WattTime | Marginal intensity + forecast | Yes (free tier) | US-focused, best marginal data |
| Electricity Maps | Average + marginal intensity | Yes (free tier) | Global coverage |
| Json | Synthetic mock | No | For dev/testing only |

Configure via environment variables or `appsettings.json`:

```json
{
  "DataSources": {
    "EmissionsDataSource": "ElectricityMaps",
    "ForecastDataSource": "ElectricityMaps",
    "Configurations": {
      "ElectricityMaps": {
        "APITokenHeader": "auth-token",
        "APIToken": "YOUR_TOKEN",
        "BaseURL": "https://api.electricitymap.org/v3/"
      }
    }
  }
}
```

---

## Software Engineering Use Cases

### 1. CI/CD Test Scheduling
Defer long-running test suites (E2E, performance, visual regression) to the next low-carbon window within a configurable deadline. Fast unit tests still run immediately.

```
PR opened → risk score assigned → fast tests run now → slow tests queued for green window
```

### 2. Batch Job Scheduling
Data pipelines, ML training jobs, and nightly reports can be shifted by 1–4 hours to hit renewables peaks.

```python
if not should_run_now("westeurope"):
    best = get_deferred_start("westeurope", window_minutes=60)
    schedule_job_at(best)
```

### 3. Multi-Region Workload Placement
For globally distributed systems, route non-latency-sensitive workloads to whichever region is currently greenest.

```http
GET /emissions/bylocations/best?location=eastus&location=northeurope&location=australiaeast
```

### 4. SCI Instrumentation
Instrument each workload with energy consumption and grid intensity at runtime to generate Software Carbon Intensity (SCI) metrics per the GSF specification.

```python
intensity = get_current_intensity(location)
energy_kwh = measure_energy_kwh()
sci = energy_kwh * intensity  # gCO₂ for this run
emit_metric("ci.carbon.grams", sci, tags={"repo": repo, "branch": branch})
```

### 5. Developer Dashboards
Surface carbon cost per PR, per repository, or per team in engineering metrics dashboards — creating awareness and enabling carbon budgets.

---

## Handling Fallbacks

The SDK or its upstream providers can be unavailable. Always define a fallback:

```python
def get_intensity_safe(location: str, fallback: float = 300.0) -> float:
    try:
        return get_current_intensity(location)
    except Exception:
        return fallback  # assume moderate-carbon grid, don't block work
```

A high fallback value (e.g., 300 gCO₂/kWh) causes the system to *prefer* deferral when data is missing — a conservative default. A low fallback runs immediately. Choose based on your risk posture.

---

## Mapping Cloud Regions to Grid Zones

The SDK accepts cloud region names and resolves them to electricity grid zones via a built-in mapping:

| Cloud Region | Grid Zone |
|-------------|-----------|
| `eastus` | `eastus` (PJM) |
| `westeurope` | `westeurope` (NL) |
| `uksouth` | `uksouth` (GB) |
| `australiaeast` | `australiaeast` (NSW) |
| `centralindia` | `centralindia` (IN-SO) |

Check the SDK's [location configuration](https://github.com/Green-Software-Foundation/carbon-aware-sdk/blob/dev/src/CarbonAware.LocationSources/src/azure-regions.json) for the full mapping.

---

## Further Reading

- [Carbon Aware SDK GitHub](https://github.com/Green-Software-Foundation/carbon-aware-sdk)
- [GSF SCI Specification](https://sci-guide.greensoftware.foundation/)
- [WattTime API Docs](https://docs.watttime.org/)
- [Electricity Maps API Docs](https://static.electricitymaps.com/api/docs/index.html)
- [Green Software Foundation Patterns](https://patterns.greensoftware.foundation/)
- Jadhav, K. (2026). *Carbon-Aware Test Scheduling in Agentic CI/CD Pipelines.* K11 Software Solutions LLC.
