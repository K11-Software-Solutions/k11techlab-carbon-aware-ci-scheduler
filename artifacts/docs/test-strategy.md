# Test Strategy — k11techlab-carbon-aware-ci-scheduler

**Version:** 1.0  
**Author:** Kavita Jadhav, K11 Software Solutions LLC  
**Date:** June 2026  
**Related repos:** `k11techlab-agentic-ai-qa-system`, `k11techlab-microservice-qa-system`

---

## 1. Scope

This document covers the test strategy for the carbon-aware CI scheduler across five phases, from isolated unit tests to full end-to-end validation against a live Carbon Aware SDK and real QA pipelines.

The system under test (SUT) consists of:

| Component | Repo | Role |
|-----------|------|------|
| `scheduler/` | carbon-aware-ci-scheduler | Core routing and scheduling logic |
| `integrations/qa_pipeline_adapter.py` | carbon-aware-ci-scheduler | Bridge to QA pipeline |
| `pipeline/runner.py` | k11techlab-agentic-ai-qa-system | QA pipeline entry point |
| `pipeline/orchestrator.py` | k11techlab-microservice-qa-system | Microservice QA target |
| Carbon Aware SDK | Docker (GSF) | Grid intensity data source |

---

## 2. Test Objectives

1. **Correctness** — Routing decisions match the defined risk × carbon matrix for all 8 combinations.
2. **No quality regression** — Deferring expensive agents to a low-carbon window produces the same verdicts as running everything immediately (target: 0% regression, matching the 180-PR study).
3. **Carbon savings accuracy** — CO2 savings estimates match the `carbon_cost_grams()` formula to within floating-point precision.
4. **Reliability** — The defer engine fires jobs at the scheduled window and accumulates CO2 savings correctly.
5. **Observability** — Metrics counters are Prometheus-compatible and increment correctly.
6. **Resilience** — The scheduler degrades gracefully when the Carbon Aware SDK is unavailable (conservative fallback, not silent skip).

---

## 3. Phase-Wise Plan

### Phase 1 — Unit Tests (no external services)

**Goal:** Verify every module in isolation. Zero external dependencies — all HTTP is mocked via `respx`, APScheduler uses in-process memory job store.

**Test files:**

| File | Module under test | Key scenarios |
|------|-------------------|---------------|
| `tests/test_risk_router.py` | `risk_router.py` | All 8 risk × carbon routing combinations, SLA deadline capping, CO2 savings sign |
| `tests/test_cost_model.py` | `cost_model.py` | Agent catalogue completeness, tier accumulation, parallel vs serial duration, formula correctness |
| `tests/test_carbon_client.py` | `carbon_client.py` | SDK unavailable fallback, optimal window selection, `can_defer_for_savings` logic, zone override |
| `tests/test_defer_engine.py` | `defer_engine.py` | Immediate fire within 5s, deferred fire at window, cancellation, timeout capping, CO2 accumulation |
| `tests/test_scheduler.py` | `scheduler.py` | submit() routing for all buckets, metrics counters, context manager cleanup |
| `tests/test_metrics.py` | `scheduler.py` + `cost_model.py` | CO2 savings formula, Prometheus key format, SCI linearity, threshold boundary |

**Entry criteria:** Python 3.11+ installed, `pip install -r requirements.txt`, `pip install respx pytest-asyncio --break-system-packages`

**Exit criteria:** All unit tests pass with `pytest -m "not e2e and not slow and not agentic"`. Coverage ≥ 85% on `scheduler/` package.

**Estimated effort:** 2–3 days (tests already written)

**Run command:**
```bash
cd k11techlab-carbon-aware-ci-scheduler
pytest tests/ -m "not e2e and not slow and not agentic" -v --tb=short
```

---

### Phase 2 — Integration Tests (stub QA pipeline)

**Goal:** Test the full submit() → route → defer chain end-to-end, with the QA pipeline in stub mode. Verifies all 8 routing scenarios and CO2 savings flow without any real QA pipeline.

**Test files:**

| File | Scenarios |
|------|-----------|
| `tests/integration/test_adapter_stub.py` | Adapter stub mode, PipelineResult fields, `_agents_to_tier()` mapping |
| `tests/integration/test_scheduler_submit.py` | All 8 routing scenarios, CO2 savings sign, multi-PR tracking, cancel_pr() |

**Infrastructure needed:** Carbon Aware SDK mocked via `respx` (no Docker needed)

**Entry criteria:** Phase 1 passes

**Exit criteria:** All 8 routing scenarios pass; `cancel_pr()` verified

**Estimated effort:** 1–2 days

**Run command:**
```bash
pytest tests/integration/ -v --tb=short
```

---

### Phase 3 — Integration Tests (real QA pipeline, mock Carbon SDK)

**Goal:** Wire `QAPipelineAdapter` to the real `k11techlab-agentic-ai-qa-system` pipeline running in memory mode. Verify verdict normalisation, timeout handling, and tier mapping.

**Test files:**

| File | Scenarios |
|------|-----------|
| `tests/integration/test_adapter_agentic_pipeline.py` | cheap/medium/full tier routing to real pipeline, timeout error handling, result normalisation |

**Infrastructure needed:**
- `k11techlab-agentic-ai-qa-system` installed or on `PYTHONPATH`
- All agentic QA dependencies installed (langgraph, langchain, etc.)
- `QA_PIPELINE_MODULE=pipeline.runner`

**Environment setup:**
```bash
export PYTHONPATH=../k11techlab-agentic-ai-qa-system:$PYTHONPATH
export QA_PIPELINE_MODULE=pipeline.runner
pip install -r ../k11techlab-agentic-ai-qa-system/requirements.txt --break-system-packages
```

**Entry criteria:** Phase 2 passes; agentic repo available

**Exit criteria:** All mock-runner tests pass; live pipeline tests pass for cheap tier in memory mode; 0% error status from clean diffs

**Estimated effort:** 2–3 days

**Run command:**
```bash
pytest tests/integration/test_adapter_agentic_pipeline.py --agentic -v
pytest tests/integration/test_adapter_agentic_pipeline.py::TestAdapterWithLiveAgenticPipeline --agentic --slow -v
```

---

### Phase 4 — End-to-End Tests (live Carbon SDK + real pipelines)

**Goal:** Full system test as deployed — real Carbon Aware SDK provides grid data, real agentic pipeline executes tests, microservice QA system is the target.

**Test files:**

| File | Scenarios |
|------|-----------|
| `tests/e2e/test_carbon_scheduling_e2e.py` | Live intensity fetch, routing decisions by risk tier, quality regression check (5 repetitions on clean diff), CO2 savings accumulation, 5-PR metrics smoke test |

**Infrastructure needed:**
```bash
# 1. Carbon Aware SDK
docker run -p 8090:8090 ghcr.io/green-software-foundation/carbon-aware-sdk:latest

# 2. Both QA repos on PYTHONPATH
export PYTHONPATH=../k11techlab-agentic-ai-qa-system:../k11techlab-microservice-qa-system:$PYTHONPATH

# 3. Environment
export QA_PIPELINE_MODULE=pipeline.runner
export CARBON_SDK_BASE_URL=http://localhost:8090
export CARBON_SDK_ZONE=eastus          # or IE, DE, GB for better variance
export CARBON_HIGH_THRESHOLD=400
```

**Key test scenarios:**

| Scenario | PR type | Risk score | Expected routing |
|----------|---------|------------|-----------------|
| Config change | LOW | 0.20 | CHEAP now; MEDIUM deferred if grid dirty |
| Feature addition | MEDIUM | 0.55 | CHEAP now; MEDIUM deferred if grid dirty |
| Auth endpoint | HIGH | 0.82 | CHEAP+MEDIUM+playwright now; perf+browser deferred |
| DB migration | HIGH | 0.82 | Same as HIGH |
| Production hotfix | CRITICAL | 0.95 | All agents now, no deferral |

**No-regression check:** Run the same clean diff through cheap tier and full tier separately. Both must PASS. Repeat 5× for stability.

**Entry criteria:** Phases 1–3 pass; Docker available; Carbon SDK container running

**Exit criteria:** All 5 routing scenarios produce correct bucket + agent split; 0/5 clean-diff runs produce FAIL; CO2 savings ≥ 0 for all deferred scenarios

**Estimated effort:** 3–5 days (including environment setup and stabilisation)

**Run command:**
```bash
pytest tests/e2e/ --e2e --slow -v --tb=long
```

---

### Phase 5 — Carbon Accounting Validation

**Goal:** Verify that the CO2 savings estimates are formula-correct, Prometheus metrics are well-formed, and the SCI (Software Carbon Intensity) components are accurate.

**Test files:** `tests/test_metrics.py`

**Key verifications:**

| Check | Method |
|-------|--------|
| CO2 savings = cost(now) − cost(optimal) | Compare `_co2_savings()` against `carbon_cost_grams()` delta |
| Savings ≥ 0 (even when optimal window is worse) | Test with reversed intensities |
| Prometheus key format: `^[a-z][a-z0-9_]*$` | Regex check all metric keys |
| SCI linearity: cost ∝ intensity | cost(2×intensity) / cost(1×intensity) ≈ 2.0 |
| CO2 units: grams not kg | 0.0001g < cost(full suite, 400 gCO2) < 100g |
| DeferEngine accumulation | Fire 2 deferred jobs, verify total = sum of savings |

**Estimated effort:** 1 day

**Run command:**
```bash
pytest tests/test_metrics.py -v
```

---

## 4. Test Pyramid Summary

```
Phase 4 (E2E)
   ████  — 5–7 scenarios, requires Docker + both QA repos
Phase 3 (Integration + real pipeline)
   ██████  — ~15 tests, requires agentic QA repo
Phase 2 (Integration + stub)
   ████████  — ~20 tests, Carbon SDK mocked via respx
Phase 1 (Unit)
   ████████████  — ~80 tests, no external dependencies
Phase 5 (Metrics)
   ███████  — ~20 tests, pure Python
```

---

## 5. Test Data

| Dataset | Source | Used in |
|---------|--------|---------|
| Synthetic PR diffs (auth, config, schema) | Hardcoded in test files | Phase 3, 4 |
| Risk scores by PR type | From 180-PR telemetry (Paper 8) | Phase 1, 2, 4 |
| Carbon intensity values | Hardcoded (200 / 400 / 500 / 600 gCO2) | Phase 1, 2 |
| Live grid intensity | Carbon Aware SDK (eastus / IE zone) | Phase 4 |
| Agent cost scores | `cost_model.py` constants | Phase 1, 5 |

---

## 6. Dependencies and Tools

| Tool | Purpose | Install |
|------|---------|---------|
| `pytest` | Test runner | `pip install pytest` |
| `pytest-asyncio` | Async test support | `pip install pytest-asyncio` |
| `respx` | httpx mock (Phase 1–2) | `pip install respx` |
| `apscheduler` | DeferEngine | Already in requirements.txt |
| Carbon Aware SDK | Live grid data (Phase 4) | Docker |
| `langchain`, `langgraph` | Agentic pipeline (Phase 3–4) | Agentic QA repo requirements |

---

## 7. CI Integration

Add to GitHub Actions (`.github/workflows/test.yml`):

```yaml
name: Test

on: [push, pull_request]

jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt respx pytest-asyncio --break-system-packages
      - run: pytest tests/ -m "not e2e and not slow and not agentic" -v

  integration-stub:
    runs-on: ubuntu-latest
    needs: unit
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt respx pytest-asyncio --break-system-packages
      - run: pytest tests/integration/ -v --tb=short
```

E2E tests (`--e2e --slow`) are not in CI — they require a live Carbon SDK and are run manually before release.

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Carbon SDK returns empty windows for eastus zone | Medium | Phase 4 tests skip | Test with IE or DE zone which have richer data |
| LangGraph pipeline non-deterministic in memory mode | Low | Phase 3 flaky | Use fixed PR diffs with predictable verdicts |
| APScheduler timing flakiness in CI (fast machines) | Low | Phase 1 flaky | Add 1s buffer to timing assertions |
| Agentic pipeline dependency conflicts with scheduler | Medium | Phase 3 install fails | Use separate venv per repo |
| Carbon intensity at test time is always low → no deferral to test | Medium | Phase 4 gap | Use zone with known high-carbon windows (DE) or inject mock |

---

## 9. Definition of Done

A phase is complete when:
- All tests in that phase pass with 0 failures
- No `ERROR` statuses in test output
- Coverage on targeted modules ≥ 85% (Phases 1–2)
- At least one routing scenario per risk bucket validated (Phase 2)
- 0% quality regression verified on clean diffs (Phase 4)
- CO2 savings formula verified against cost model (Phase 5)
