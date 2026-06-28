# Test Plan

**Project:** K11tech Carbon-Aware CI Scheduler
**Version:** 1.0
**Date:** 2026-06-24
**Author:** K11 Software Solutions LLC
**References:** FRD v1.0, BRD v1.0

---

## 1. Scope

This test plan covers functional, integration, and edge-case testing for all modules of the Carbon-Aware CI Scheduler:

- `CarbonAwareScheduler` (scheduler.py)
- `RiskRouter` (risk_router.py)
- `CarbonAwareClient` (carbon_client.py)
- `DeferEngine` (defer_engine.py)
- `cost_model.py`
- `QAPipelineAdapter` (integrations/qa_pipeline_adapter.py)

**Out of scope:** Carbon Aware SDK internals, WattTime/Electricity Maps upstream APIs, GitHub Actions runner behaviour.

---

## 2. Test Strategy

| Layer | Approach | Tooling |
|-------|----------|---------|
| Unit | Isolated module testing with mocked dependencies | pytest, unittest.mock |
| Integration | Multi-module flows with a mock Carbon SDK and mock run_fn | pytest, httpretty or respx |
| Contract | Verify QAPipelineAdapter conforms to the run_agents interface | pytest |
| Edge / boundary | Risk score boundaries, carbon threshold transitions, SLA deadline edge cases | pytest parametrize |
| Observability | Verify metrics output shape and counter increments | pytest |

No end-to-end tests against live Carbon SDK or live QA pipeline are in scope for CI — those are run manually in the staging environment.

---

## 3. Test Environment

| Requirement | Detail |
|-------------|--------|
| Python version | 3.10+ |
| Test runner | `pytest` with `pytest-asyncio` for async tests |
| Carbon SDK | Not required — mocked via `respx` (async httpx mock) |
| APScheduler | Real instance used in integration tests; `threading.Event` for synchronisation |
| CI execution | GitHub Actions — `ubuntu-latest` |

**Setup:**
```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio respx
pytest tests/
```

---

## 4. Unit Test Cases

### 4.1 `cost_model.py`

| TC-ID | Test | Expected Result |
|-------|------|----------------|
| CM-01 | `agents_for_tier(Tier.CHEAP)` returns only CHEAP agents | 5 agents: api, security, data, a11y, regression |
| CM-02 | `agents_for_tier(Tier.MEDIUM)` returns CHEAP + MEDIUM agents | 7 agents |
| CM-03 | `agents_for_tier(Tier.FULL)` returns all 10 agents | All agents in AGENT_COSTS |
| CM-04 | `total_cost(all_agents)` > `total_cost(cheap_agents)` | True |
| CM-05 | `estimated_duration_s(cheap_only)` = max of CHEAP durations | ~5.1 s |
| CM-06 | `estimated_duration_s(full_suite)` = parallel cheap+medium + serial full | ~5.1 + (38.5+52.3+34.1) = ~130 s |
| CM-07 | `carbon_cost_grams(agents, 0)` = 0.0 | Zero carbon on zero-intensity grid |
| CM-08 | `carbon_cost_grams([], 400)` = 0.0 | Zero cost with empty agent list |
| CM-09 | `carbon_cost_grams(full_suite, 400)` > `carbon_cost_grams(cheap_only, 400)` | Full suite costs more |

### 4.2 `RiskRouter` — risk classification

| TC-ID | Score | Expected Bucket |
|-------|-------|----------------|
| RR-01 | 0.00 | LOW |
| RR-02 | 0.39 | LOW |
| RR-03 | 0.40 | MEDIUM |
| RR-04 | 0.69 | MEDIUM |
| RR-05 | 0.70 | HIGH |
| RR-06 | 0.89 | HIGH |
| RR-07 | 0.90 | CRITICAL |
| RR-08 | 1.00 | CRITICAL |

### 4.3 `RiskRouter` — routing decisions (low carbon grid)

Grid: `current_intensity = 200`, `is_high_carbon_now = False`

| TC-ID | Risk Score | Expected Immediate | Expected Deferred |
|-------|-----------|-------------------|------------------|
| RR-10 | 0.20 (LOW) | CHEAP tier only | [] |
| RR-11 | 0.55 (MEDIUM) | CHEAP + MEDIUM | [] |
| RR-12 | 0.75 (HIGH) | ALL agents | [] |
| RR-13 | 0.95 (CRITICAL) | ALL agents | [] |

### 4.4 `RiskRouter` — routing decisions (high carbon grid)

Grid: `current_intensity = 450`, `is_high_carbon_now = True`

| TC-ID | Risk Score | Expected Immediate | Expected Deferred |
|-------|-----------|-------------------|------------------|
| RR-20 | 0.20 (LOW) | CHEAP tier | MEDIUM tier |
| RR-21 | 0.55 (MEDIUM) | CHEAP tier | MEDIUM tier |
| RR-22 | 0.75 (HIGH) | CHEAP + MEDIUM + playwright_agent | perf_agent, browser_agent |
| RR-23 | 0.95 (CRITICAL) | ALL agents | [] — CRITICAL overrides carbon |

### 4.5 `RiskRouter` — force_full flag

| TC-ID | force_full | Risk Score | Expected |
|-------|-----------|-----------|----------|
| RR-30 | True | 0.20 | All agents immediate, no deferral, reason = "force_full override" |
| RR-31 | True | 0.95 | All agents immediate, no deferral |

### 4.6 `RiskRouter` — SLA deadline guard

| TC-ID | Scenario | Expected |
|-------|----------|----------|
| RR-40 | Optimal window is 2h from now, SLA deadline is 1h from now | Picks next window before deadline |
| RR-41 | All forecast windows are after SLA deadline | Returns None deferred_window; falls back to immediate |

### 4.7 `RiskRouter` — CO₂ savings estimate

| TC-ID | Scenario | Expected |
|-------|----------|----------|
| RR-50 | Deferred agents, current=450, optimal=100 | savings > 0.0 |
| RR-51 | No deferred agents | savings = 0.0 |
| RR-52 | No optimal window | savings = 0.0 |

---

## 5. Integration Test Cases

### 5.1 `CarbonAwareScheduler.submit()` — full flow

| TC-ID | Scenario | Expected |
|-------|----------|----------|
| INT-01 | LOW risk, low carbon | Immediate CHEAP jobs scheduled; no deferred jobs |
| INT-02 | MEDIUM risk, high carbon | Immediate CHEAP jobs + deferred MEDIUM jobs |
| INT-03 | CRITICAL risk, high carbon | All agents immediate; no deferred jobs |
| INT-04 | force_full=True, low risk | All agents immediate; no deferred jobs |
| INT-05 | Carbon SDK unavailable (timeout) | Fallback intensity used; scheduling continues without error |
| INT-06 | run_fn raises exception on fire | Exception is caught and logged; scheduler remains alive |
| INT-07 | Submit two PRs concurrently | Both PRs scheduled independently with unique job IDs |

### 5.2 `CarbonAwareScheduler.cancel_pr()`

| TC-ID | Scenario | Expected |
|-------|----------|----------|
| INT-10 | Cancel PR with 1 pending deferred job | 1 job cancelled, job record marked cancelled=True |
| INT-11 | Cancel PR with no pending jobs | Returns 0 |
| INT-12 | Cancel PR after deferred job has already fired | Returns 0 (fired jobs not reverted) |

### 5.3 `DeferEngine` timing

| TC-ID | Scenario | Expected |
|-------|----------|----------|
| INT-20 | Immediate job scheduled | Fires within 5 seconds |
| INT-21 | Deferred job with `run_at` 3 seconds from now | Fires within 5 seconds of `run_at` |
| INT-22 | Deferred window beyond DEFERRED_TIMEOUT_HOURS | Capped to `now + DEFERRED_TIMEOUT_HOURS` |
| INT-23 | Cancel deferred job before it fires | run_fn not called |

---

## 6. Edge and Boundary Cases

| TC-ID | Scenario | Expected |
|-------|----------|----------|
| EDGE-01 | `risk_score = 0.0` | LOW bucket; CHEAP agents only |
| EDGE-02 | `risk_score = 1.0` | CRITICAL bucket; all agents immediate |
| EDGE-03 | `risk_score = 0.40` (exact boundary) | MEDIUM bucket |
| EDGE-04 | `risk_score = 0.70` (exact boundary) | HIGH bucket |
| EDGE-05 | `risk_score = 0.90` (exact boundary) | CRITICAL bucket |
| EDGE-06 | Carbon intensity = `CARBON_HIGH_THRESHOLD` exactly | Treated as NOT high carbon (threshold is strict `>`) |
| EDGE-07 | Carbon intensity = `CARBON_HIGH_THRESHOLD + 0.1` | Treated as high carbon |
| EDGE-08 | Empty `forecast.windows` list | Falls back to `forecast.optimal_window` |
| EDGE-09 | `forecast.optimal_window = None` and `windows = []` | Immediate scheduling; no deferral attempted |
| EDGE-10 | `sla_deadline` = now (already expired) | No deferral; run immediately |
| EDGE-11 | `pr_id` contains special characters (`PR/42#test`) | Sanitised in job ID via uuid suffix; no crash |
| EDGE-12 | `metadata` dict with 100 keys | Passed through without truncation |
| EDGE-13 | Scheduler not started before `submit()` | APScheduler raises; error surfaces clearly |

---

## 7. Observability / Metrics Tests

| TC-ID | Scenario | Expected |
|-------|----------|----------|
| OBS-01 | Submit 3 PRs | `carbon_scheduler_prs_submitted_total` = 3 |
| OBS-02 | 2 PRs with deferred jobs fire | `carbon_scheduler_co2_saved_grams_total` > 0 |
| OBS-03 | 1 pending deferred job | `carbon_scheduler_jobs_pending` = 1 |
| OBS-04 | Cancel pending job | `carbon_scheduler_jobs_pending` decrements |

---

## 8. Existing Test Coverage

The file [tests/test_risk_router.py](../../tests/test_risk_router.py) covers:

- Risk bucket classification (boundary values)
- `RoutingDecision.has_deferred_jobs` property
- `RoutingDecision.summary()` output format
- High-carbon routing for LOW, MEDIUM, HIGH, CRITICAL buckets
- Low-carbon no-deferral path
- CRITICAL + force_full override paths
- `_co2_savings` calculation
- `_pick_window` SLA guard

**Coverage gaps to address:**

| Gap | Priority |
|-----|---------|
| `DeferEngine` timing tests (INT-20 to INT-23) | High |
| `CarbonAwareClient` fallback on SDK timeout | High |
| Full `submit()` integration flow (INT-01 to INT-07) | High |
| Metrics counter correctness (OBS-01 to OBS-04) | Medium |
| `QAPipelineAdapter` contract test | Medium |
| `cost_model` edge cases (CM-07, CM-08) | Low |

---

## 9. Test Data

### Mock Carbon Forecast (Low Carbon)
```python
CarbonForecast(
    current_intensity=150.0,
    is_high_carbon_now=False,
    optimal_window=CarbonWindow(start=now+timedelta(hours=1), intensity=120.0),
    windows=[...],
)
```

### Mock Carbon Forecast (High Carbon)
```python
CarbonForecast(
    current_intensity=450.0,
    is_high_carbon_now=True,
    optimal_window=CarbonWindow(start=now+timedelta(hours=3), intensity=90.0),
    windows=[
        CarbonWindow(start=now+timedelta(hours=2), intensity=300.0),
        CarbonWindow(start=now+timedelta(hours=3), intensity=90.0),
        CarbonWindow(start=now+timedelta(hours=5), intensity=120.0),
    ],
)
```

### Standard PREvent Fixtures

```python
PR_LOW    = PREvent(pr_id="PR-LOW",    risk_score=0.20)
PR_MEDIUM = PREvent(pr_id="PR-MED",    risk_score=0.55)
PR_HIGH   = PREvent(pr_id="PR-HIGH",   risk_score=0.75)
PR_CRIT   = PREvent(pr_id="PR-CRIT",   risk_score=0.95)
PR_FORCE  = PREvent(pr_id="PR-FORCE",  risk_score=0.10, force_full=True)
```

---

## 10. Pass / Fail Criteria

| Criterion | Pass Threshold |
|-----------|---------------|
| All unit tests | 100% pass |
| All integration tests | 100% pass |
| Edge/boundary tests | 100% pass |
| Code coverage (pytest-cov) | ≥ 85% line coverage across scheduler/ |
| No deferred test causes a missed defect | 0 failures attributed to deferral in pilot |
| Carbon reduction vs. always-immediate baseline | ≥ 25% in 180-PR sample |
