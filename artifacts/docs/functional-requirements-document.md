# Functional Requirements Document (FRD)

**Project:** K11tech Carbon-Aware CI Scheduler
**Version:** 1.0
**Date:** 2026-06-24
**Author:** K11 Software Solutions LLC
**References:** BRD v1.0, carbon-aware-sdk-guide.md

---

## 1. System Overview

The Carbon-Aware CI Scheduler is a Python service that sits between a CI webhook (e.g., GitHub Actions PR event) and the K11tech QA pipeline. It classifies each incoming PR by risk score, queries the Carbon Aware SDK for grid intensity, and schedules test agents either immediately or at the next low-carbon window within a configurable deadline.

```
GitHub PR Event
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│                  CarbonAwareScheduler                   │
│                                                         │
│  PREvent ──► RiskRouter ──► RoutingDecision             │
│                 │                 │                     │
│         CarbonAwareClient    DeferEngine                │
│         (SDK forecast)    (APScheduler)                 │
│                                   │                     │
└───────────────────────────────────┼─────────────────────┘
                                    ▼
                          QAPipelineAdapter.run_agents()
```

---

## 2. Modules and Responsibilities

### 2.1 `CarbonAwareScheduler` (scheduler.py)

The top-level orchestrator. Exposes three public entry points.

| Method | Signature | Description |
|--------|-----------|-------------|
| `submit` | `async submit(pr: PREvent) → RoutingDecision` | Main entry point for an incoming PR webhook. Fetches forecast, routes, and schedules jobs. |
| `cancel_pr` | `cancel_pr(pr_id: str) → int` | Cancels all pending jobs for a PR when it is closed or superseded. Returns count cancelled. |
| `metrics` | `metrics() → dict` | Returns Prometheus-compatible metric counters. |

**PREvent fields:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `pr_id` | str | Yes | — | Unique identifier for the pull request |
| `risk_score` | float [0.0–1.0] | Yes | — | QA risk score from the upstream pipeline |
| `zone` | str | No | `settings.DEFAULT_ZONE` | Grid zone override (e.g., `IE`, `eastus`) |
| `sla_deadline` | datetime | No | `now + 8h` | Hard ceiling on deferral time |
| `force_full` | bool | No | `False` | Bypasses carbon deferral; runs full suite immediately |
| `metadata` | dict | No | `{}` | Arbitrary key–value pairs forwarded to run_fn |

---

### 2.2 `RiskRouter` (risk_router.py)

Classifies a PR into a risk bucket and determines which agents run immediately and which are deferred.

#### 2.2.1 Risk Buckets

| Bucket | Score Range | Immediate Agents | Deferrable Agents |
|--------|-------------|-----------------|------------------|
| LOW | < 0.40 | CHEAP tier | MEDIUM tier |
| MEDIUM | 0.40 – 0.69 | CHEAP tier | MEDIUM tier |
| HIGH | 0.70 – 0.89 | CHEAP + MEDIUM + playwright_agent | perf_agent, browser_agent |
| CRITICAL | ≥ 0.90 | ALL agents | None (never deferred) |

#### 2.2.2 Carbon Gate

- If `current_intensity < CARBON_HIGH_THRESHOLD` (default: 400 gCO₂/kWh), no deferral occurs regardless of risk bucket.
- If `current_intensity ≥ CARBON_HIGH_THRESHOLD`, deferral rules in §2.2.1 apply.

#### 2.2.3 SLA Guard

- The optimal window is selected from the lowest-intensity forecast window whose `start` timestamp is before `sla_deadline`.
- If no window falls before the deadline, the earliest available window before the deadline is used.
- If `force_full=True` or `risk_bucket == CRITICAL`, routing returns all agents as immediate with no deferral.

#### 2.2.4 `RoutingDecision` Output

| Field | Type | Description |
|-------|------|-------------|
| `immediate_agents` | list[str] | Agent names to run now |
| `deferred_agents` | list[str] | Agent names to defer |
| `deferred_window` | CarbonWindow | The selected low-carbon execution window |
| `risk_bucket` | RiskBucket | Classified bucket |
| `carbon_intensity` | float | Current grid intensity at decision time |
| `deferral_reason` | str | Human-readable explanation |
| `estimated_savings_g_co2` | float | Estimated CO₂ saved by deferral |

---

### 2.3 `CarbonAwareClient` (carbon_client.py)

Wraps the Carbon Aware SDK REST API.

| Method | Description |
|--------|-------------|
| `get_forecast(zone)` | Returns `CarbonForecast` with current intensity + list of future windows |
| `close()` | Closes the underlying HTTP session |

**CarbonForecast fields:**

| Field | Type | Description |
|-------|------|-------------|
| `current_intensity` | float | Grid intensity now (gCO₂/kWh) |
| `optimal_window` | CarbonWindow | Best window in the lookahead period |
| `windows` | list[CarbonWindow] | All forecast windows |
| `is_high_carbon_now` | bool | True if `current_intensity ≥ CARBON_HIGH_THRESHOLD` |

**Fallback behaviour:** If the SDK is unreachable, `get_forecast()` returns a synthetic `CarbonForecast` using the `CARBON_FALLBACK_INTENSITY` config value (default: 300 gCO₂/kWh). CI is never blocked by SDK unavailability.

---

### 2.4 `DeferEngine` (defer_engine.py)

APScheduler-backed job store. Manages immediate and deferred job execution.

| Method | Description |
|--------|-------------|
| `start()` | Starts the BackgroundScheduler |
| `stop()` | Shuts down the scheduler, optionally waiting for running jobs |
| `schedule_immediate(pr_id, agents, meta)` | Fires within 2 seconds of call |
| `schedule_deferred(pr_id, agents, run_at, carbon_g_saved, meta)` | Fires at `run_at`; caps at `DEFERRED_TIMEOUT_HOURS` from now |
| `cancel(job_id)` | Cancels a single job |
| `cancel_pr(pr_id)` | Cancels all pending jobs for a PR |
| `status()` | Returns list of `JobRecord` dicts for observability |
| `total_co2_saved_g()` | Returns cumulative CO₂ saved by all fired deferred jobs |

**Job ID format:**
- Immediate: `imm_{pr_id}_{8-char-uuid}`
- Deferred: `def_{pr_id}_{8-char-uuid}`

**Timeout safety net:** Any deferred job that has not fired within `DEFERRED_TIMEOUT_HOURS` (default: 8) fires unconditionally.

---

### 2.5 Agent Cost Model (cost_model.py)

Defines per-agent compute cost, tier membership, and duration estimates used to calculate CO₂ savings.

#### 2.5.1 Agent Tiers

| Tier | Agents | Avg Duration | Cost Score Range |
|------|--------|-------------|-----------------|
| CHEAP | api_agent, security_agent, data_agent, a11y_agent, regression_agent | 3.8–5.1 s | 0.15–0.22 |
| MEDIUM | cross_repo_impact_agent, drift_analysis_agent | 8.7–9.4 s | 0.35–0.38 |
| FULL | playwright_agent, perf_agent, browser_agent | 34–52 s | 0.68–0.85 |

#### 2.5.2 Carbon Cost Formula

```
energy_kWh = (duration_hours × tdp_watts × pue) / 1000
carbon_g   = energy_kWh × carbon_intensity (gCO₂eq/kWh)
```

Defaults: `tdp_watts = 45`, `pue = 1.4`

CHEAP/MEDIUM agents are modelled as parallel (wall time = max); FULL-tier agents run serially.

---

### 2.6 `QAPipelineAdapter` (integrations/qa_pipeline_adapter.py)

Adapter between the scheduler and the K11tech QA pipeline. The scheduler calls:

```python
await adapter.run_agents(pr_id: str, agents: list[str], meta: dict) -> dict
```

The adapter is responsible for translating `agents` (internal names) to the pipeline's own invocation format. If the adapter is not installed, a no-op function is used and a warning is logged — the scheduler continues to operate.

---

## 3. Configuration

All configuration is read from `config/settings.py` which loads from environment variables or `.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `CARBON_SDK_URL` | `http://localhost:8090` | Carbon Aware SDK WebAPI base URL |
| `DEFAULT_ZONE` | `eastus` | Default grid zone for carbon queries |
| `CARBON_HIGH_THRESHOLD` | `400` | gCO₂/kWh above which deferral activates |
| `CARBON_FALLBACK_INTENSITY` | `300` | Used when SDK is unreachable |
| `DEFERRED_TIMEOUT_HOURS` | `8` | Maximum deferral horizon in hours |
| `FORECAST_LOOKAHEAD_HOURS` | `8` | How far ahead to request forecast windows |
| `FORECAST_WINDOW_MINUTES` | `30` | Window size for optimal window query |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## 4. Interfaces

### 4.1 Webhook (GitHub Actions)

Incoming CI events reach the scheduler via a POST webhook or direct Python call:

```python
from scheduler.scheduler import CarbonAwareScheduler, PREvent

async with CarbonAwareScheduler() as sched:
    decision = await sched.submit(PREvent(
        pr_id="PR-42",
        risk_score=0.65,
        zone="IE",
    ))
```

### 4.2 CLI

```bash
python -m scheduler.scheduler submit --pr-id PR-42 --risk-score 0.65 --zone IE
python -m scheduler.scheduler metrics
```

### 4.3 Metrics Endpoint

`scheduler.metrics()` returns a dict keyed by Prometheus-style metric names:

```json
{
  "carbon_scheduler_prs_submitted_total": 45,
  "carbon_scheduler_immediate_jobs_total": 45,
  "carbon_scheduler_deferred_jobs_total": 17,
  "carbon_scheduler_co2_saved_grams_total": 214.3,
  "carbon_scheduler_jobs_pending": 3
}
```

---

## 5. Functional Rules

| ID | Rule |
|----|------|
| FR-01 | `risk_score` must be in [0.0, 1.0]. Values outside this range are clamped before routing. |
| FR-02 | A PR with `force_full=True` always runs all agents immediately, regardless of risk score or carbon intensity. |
| FR-03 | A CRITICAL PR (risk ≥ 0.90) never has any agents deferred. |
| FR-04 | Deferred jobs are capped at `now + DEFERRED_TIMEOUT_HOURS`. No job is held indefinitely. |
| FR-05 | If the Carbon Aware SDK returns no forecast windows, the scheduler uses the optimal_window from the forecast object; if that is also absent, immediate scheduling applies. |
| FR-06 | `cancel_pr()` cancels only pending (not yet fired) jobs. Already-fired jobs are not reverted. |
| FR-07 | CHEAP-tier agents always run immediately for all risk buckets. |
| FR-08 | CO₂ savings are estimated, not measured. Actuals require external energy metering. |
| FR-09 | Job IDs are unique per-PR-per-submission. A new commit to an open PR creates new job IDs without cancelling existing pending jobs (unless `cancel_pr` is called). |
| FR-10 | All timestamps are stored and compared in UTC. |
