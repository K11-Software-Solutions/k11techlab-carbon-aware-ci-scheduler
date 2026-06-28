# Test Evaluation Results

**Project:** K11tech Carbon-Aware CI Scheduler  
**Evaluation dates:** 2026-06-24 to 2026-06-25  
**Evaluator:** Codex  
**Source strategy:** [test-strategy.md](./test-strategy.md)  
**Detailed Phase 4 run log:** [phase-4-step-by-step-test-run-log.md](./phase-4-step-by-step-test-run-log.md)  
**Empirical result artifacts:** [../results](../results)

---

## Summary

| Phase | Scope | Command | Result |
|-------|-------|---------|--------|
| Phase 1 | Unit tests, excluding e2e, slow, and agentic tests | `python -m pytest tests/ -m "not e2e and not slow and not agentic" -v --tb=short` | PASS - 130 passed, 21 deselected |
| Phase 2 | Stub QA pipeline integration tests | `python -m pytest tests/integration/ -v --tb=short` | PASS - 26 passed, 9 deselected |
| Phase 3 | Agentic QA pipeline adapter integration tests | `python -m pytest tests/integration/test_adapter_agentic_pipeline.py -o addopts="" -m "agentic" -v --tb=short` | PASS - 9 passed |
| Phase 4 | End-to-end tests with Carbon SDK container and real QA pipeline repos | `python -m pytest tests/e2e/ -o addopts="" -m "e2e and slow" -v --tb=long` | PASS - 12 passed |
| Phase 5 | Carbon accounting and metrics validation | `python -m pytest tests/test_metrics.py -v` | PASS - 20 passed |

Phases 1, 2, 3, 4, and 5 passed. Phase 1 required installing the missing documented test dependency `respx`. Phase 4 required Docker Desktop startup and Carbon SDK JSON datasource configuration.

---

## Environment

| Item | Value |
|------|-------|
| OS | Windows 10 |
| Python | 3.11.0 |
| Pytest | 8.4.2 |
| pytest-asyncio | 1.3.0 |
| respx | 0.23.1 |
| Working directory | `C:\Users\kavit\Automation\K11tech\k11techlab-carbon-aware-ci-scheduler` |

---

## Setup Action

The first Phase 1 attempt stopped during test collection because `respx` was not installed:

```text
ModuleNotFoundError: No module named 'respx'
```

Installed the documented dependency:

```bash
python -m pip install respx pytest-asyncio
```

`pytest-asyncio` was already present; `respx` was installed successfully.

---

## Phase 1 Result

Command:

```bash
python -m pytest tests/ -m "not e2e and not slow and not agentic" -v --tb=short
```

Outcome:

```text
130 passed, 21 deselected in 30.08s
```

Notes:

- This marker-based Phase 1 command also collected the stub integration tests because those tests are not excluded by the marker expression.
- No failures or errors were reported after dependency setup.
- Coverage was not measured because the strategy command does not include `--cov`.

---

## Phase 2 Result

Command:

```bash
python -m pytest tests/integration/ -v --tb=short
```

Outcome:

```text
26 passed, 9 deselected in 4.35s
```

Validated areas:

- Adapter stub mode and `PipelineResult` fields
- Agent-to-tier mapping
- All 8 routing scenarios
- CO2 savings sign behavior
- Multi-PR tracking
- `cancel_pr()` behavior

---

## Phase 3 Result

Phase 3 was run with the sibling `k11techlab-agentic-ai-qa-system` repository on `PYTHONPATH` and `QA_PIPELINE_MODULE=pipeline.runner`.

The documented `--agentic` and `--slow` flags are not registered as pytest CLI options in this repository. The run used marker selection with the repository default `addopts` cleared, because `pytest.ini` otherwise excludes `agentic` and `slow` tests by default.

Mock-runner slice:

```bash
python -m pytest tests/integration/test_adapter_agentic_pipeline.py -o addopts="" -m "agentic and not slow" -v --tb=short
```

Outcome:

```text
6 passed, 3 deselected in 1.09s
```

Live agentic pipeline slice:

```bash
python -m pytest tests/integration/test_adapter_agentic_pipeline.py::TestAdapterWithLiveAgenticPipeline -o addopts="" -v --tb=short
```

Outcome:

```text
3 passed in 2.30s
```

Full Phase 3 run:

```bash
python -m pytest tests/integration/test_adapter_agentic_pipeline.py -o addopts="" -m "agentic" -v --tb=short
```

Outcome:

```text
9 passed in 3.50s
```

Setup/fix note:

- Initial mock-runner run produced 1 failure in `test_run_agents_timeout_handled`.
- Root cause: the class autouse fixture patched `importlib.import_module`, and the timeout test tried to nest another string-target patch for the same function.
- Fix: the autouse fixture now yields without patching for `test_run_agents_timeout_handled`, allowing that test to install its own slow-runner patch.

Validated areas:

- Cheap, medium, and full agent tier mapping to the agentic pipeline interface
- Timeout handling and result normalisation
- Live cheap-tier pipeline execution in memory mode
- Live full-suite verdict normalisation
- No error status for cheap/full deferral regression check

---
## Phase 4 Result

Phase 4 was run with Docker Desktop, the sibling `k11techlab-agentic-ai-qa-system` and `k11techlab-microservice-qa-system` repositories on `PYTHONPATH`, and `QA_PIPELINE_MODULE=pipeline.runner`.

The documented `--e2e` and `--slow` flags are not registered as pytest CLI options in this repository. The run used marker selection with the repository default `addopts` cleared, because `pytest.ini` otherwise excludes `e2e` and `slow` tests by default.

Command:

```bash
python -m pytest tests/e2e/ -o addopts="" -m "e2e and slow" -v --tb=long
```

Outcome:

```text
12 passed in 4.94s
```

SDK setup notes:

- Docker Desktop was installed but not initially running; it was launched before the SDK container could start.
- `ghcr.io/green-software-foundation/carbon-aware-sdk:latest` pulled successfully, but the bare strategy command exited with `No data sources are configured`.
- `ghcr.io/green-software-foundation/carbon-aware-sdk-api:latest` was denied by the registry.
- The available `carbon-aware-sdk:latest` image listens on container port `8080`, so it was mapped as `8090:8080`.
- The SDK JSON datasource supports emissions only, not forecast data. The final container used `DataSources__EmissionsDataSource=Json` with a mounted local `eastus.json` emissions file.
- The readiness endpoint returned HTTP `204`; the scheduler client handled missing response data through its conservative fallback path, and forecast windows were allowed to be empty.

Validated areas:

- E2E routing checks for LOW, HIGH, and CRITICAL risk PRs
- Live agentic QA adapter calls in memory mode
- No quality-regression checks across cheap/full deferred tiers
- Carbon savings and metrics smoke checks
- Microservice QA integration smoke checks

---
## Phase 5 Result

Phase 5 validated carbon accounting, metrics counters, threshold behavior, and SCI scaling through `tests/test_metrics.py`.

Command:

```bash
python -m pytest tests/test_metrics.py -v
```

Outcome:

```text
20 passed in 7.15s
```

Validated areas:

- CO2 savings formula behavior and `carbon_cost_grams()` delta matching
- Scheduler metrics counters and Prometheus-compatible metric keys
- DeferEngine CO2 accumulation behavior
- Carbon intensity threshold boundaries
- SCI linearity with intensity, PUE, and TDP
- CO2 units reported in grams rather than kg

---
## Exit Criteria Status

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Phase 1 tests pass | PASS | `130 passed, 21 deselected` |
| Phase 2 tests pass | PASS | `26 passed, 9 deselected` |
| Phase 3 mock-runner tests pass | PASS | `6 passed, 3 deselected` |
| Phase 3 live pipeline tests pass | PASS | `3 passed` |
| Phase 3 full file passes | PASS | `9 passed` |
| Phase 4 e2e tests pass | PASS | `12 passed` |
| Phase 4 quality regression check passes | PASS | `TestNoQualityRegressionE2E` passed |
| Phase 5 carbon accounting tests pass | PASS | `20 passed` |
| CO2 savings formula verified against cost model | PASS | `test_savings_matches_cost_model_delta` passed |
| All 8 routing scenarios pass | PASS | `TestAllRoutingScenarios` passed in Phase 2 |
| `cancel_pr()` verified | PASS | `test_cancel_pr_after_submit` passed |
| Coverage >= 85% on `scheduler/` | NOT MEASURED | Coverage was not part of the documented run commands |

