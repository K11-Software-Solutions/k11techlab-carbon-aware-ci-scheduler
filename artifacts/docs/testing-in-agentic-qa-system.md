# Testing the Scheduler in the Agentic QA System

> A practical guide to testing the Carbon-Aware CI Scheduler end-to-end within the K11tech Agentic AI QA System.

---

## 1. Integration Architecture

The scheduler connects to the agentic QA system through one seam: `QAPipelineAdapter` → `k11techlab.runner.run_pipeline()`.

```
pytest / CI webhook
      │
      ▼
CarbonAwareScheduler.submit(pr)
      │
      ├── CarbonAwareClient (mocked or real SDK)
      ├── RiskRouter (real)
      └── DeferEngine
              │
              ▼
       QAPipelineAdapter.run_agents()
              │
              ├── [stub mode]  → logs + returns synthetic PASS
              └── [real mode]  → k11techlab.runner.run_pipeline()
                                        │
                                        ▼
                               Agentic QA pipeline executes
                               selected agents against the PR
```

The `QA_PIPELINE_MODULE` environment variable switches between stub and real:

```bash
# Stub mode (dev/unit testing — no QA system needed)
QA_PIPELINE_MODULE=k11techlab.runner.stub

# Real mode (integration testing — QA system must be installed)
QA_PIPELINE_MODULE=k11techlab.runner
```

---

## 2. Testing Layers

| Layer | What's tested | QA pipeline required? |
|-------|--------------|----------------------|
| Unit | RiskRouter, cost_model, DeferEngine internals | No |
| Adapter contract | QAPipelineAdapter ↔ run_pipeline() interface | No (mock) |
| Scheduler integration | Full submit() flow with stub pipeline | No |
| Pipeline integration | Scheduler drives real agentic QA agents | Yes |
| End-to-end | Webhook → scheduler → agents → verdict | Yes |

---

## 3. Unit Tests (No QA System Needed)

These already exist in `tests/test_risk_router.py`. Extend them for cost model and defer engine:

```python
# tests/test_cost_model.py
from scheduler.cost_model import agents_for_tier, carbon_cost_grams, Tier

def test_cheap_agents_only_in_cheap_tier():
    from scheduler.cost_model import AGENT_COSTS
    agents = agents_for_tier(Tier.CHEAP)
    assert all(AGENT_COSTS[a].tier == Tier.CHEAP for a in agents)

def test_carbon_cost_zero_on_clean_grid():
    agents = agents_for_tier(Tier.FULL)
    assert carbon_cost_grams(agents, carbon_intensity=0.0) == 0.0

def test_full_suite_costs_more_than_cheap():
    full  = carbon_cost_grams(agents_for_tier(Tier.FULL),  400.0)
    cheap = carbon_cost_grams(agents_for_tier(Tier.CHEAP), 400.0)
    assert full > cheap
```

```python
# tests/test_defer_engine.py
import asyncio, threading, time
import pytest
from scheduler.defer_engine import DeferEngine

@pytest.fixture
def engine():
    fired = []
    async def run_fn(pr_id, agents, meta):
        fired.append({"pr_id": pr_id, "agents": agents})
    e = DeferEngine(run_fn=run_fn)
    e.start()
    yield e, fired
    e.stop()

def test_immediate_job_fires(engine):
    e, fired = engine
    e.schedule_immediate(pr_id="PR-1", agents=["api_agent"])
    time.sleep(3)
    assert any(j["pr_id"] == "PR-1" for j in fired)

def test_cancel_prevents_fire(engine):
    e, fired = engine
    from datetime import datetime, timedelta, timezone
    run_at = datetime.now(timezone.utc) + timedelta(seconds=10)
    job_id = e.schedule_deferred(pr_id="PR-2", agents=["perf_agent"], run_at=run_at)
    e.cancel(job_id)
    time.sleep(2)
    assert not any(j["pr_id"] == "PR-2" for j in fired)
```

---

## 4. Adapter Contract Tests

Verify the `QAPipelineAdapter` correctly calls `run_pipeline()` with the right arguments, without needing the full QA system installed.

```python
# tests/test_qa_pipeline_adapter.py
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from integrations.qa_pipeline_adapter import QAPipelineAdapter, PipelineResult

@pytest.fixture
def mock_runner():
    """Simulate k11techlab.runner with a mock run_pipeline."""
    mock = AsyncMock(return_value={
        "status": "passed",
        "verdict": "PASS",
        "duration_s": 4.2,
    })
    return mock

@pytest.mark.asyncio
async def test_cheap_agents_map_to_cheap_tier(mock_runner):
    adapter = QAPipelineAdapter()
    adapter._is_stub = False

    import types
    module = types.ModuleType("k11techlab.runner")
    module.run_pipeline = mock_runner
    adapter._runner_module = module

    result = await adapter.run_agents(
        pr_id="PR-42",
        agents=["api_agent", "security_agent"],
    )
    call_kwargs = mock_runner.call_args.kwargs
    assert call_kwargs["agent_tier"] == "cheap"
    assert call_kwargs["pr_id"] == "PR-42"
    assert result.verdict == "PASS"

@pytest.mark.asyncio
async def test_full_tier_agent_maps_to_full(mock_runner):
    adapter = QAPipelineAdapter()
    adapter._is_stub = False

    import types
    module = types.ModuleType("k11techlab.runner")
    module.run_pipeline = mock_runner
    adapter._runner_module = module

    await adapter.run_agents(pr_id="PR-43", agents=["perf_agent"])
    assert mock_runner.call_args.kwargs["agent_tier"] == "full"

@pytest.mark.asyncio
async def test_stub_returns_pass_without_qa_system():
    adapter = QAPipelineAdapter()
    # Force stub mode — QA system not installed
    adapter._is_stub = True
    adapter._runner_module = object()

    result = await adapter.run_agents(pr_id="PR-99", agents=["api_agent"])
    assert result.status == "stub"
    assert result.verdict == "PASS"

@pytest.mark.asyncio
async def test_timeout_returns_error_result(mock_runner):
    import asyncio
    mock_runner.side_effect = asyncio.TimeoutError()
    adapter = QAPipelineAdapter()
    adapter._is_stub = False

    import types
    module = types.ModuleType("k11techlab.runner")
    module.run_pipeline = mock_runner
    adapter._runner_module = module

    result = await adapter.run_agents(pr_id="PR-44", agents=["perf_agent"])
    assert result.status == "error"
    assert result.verdict == "FAIL"
```

---

## 5. Scheduler Integration Tests (Stub Pipeline)

Test the full `submit()` flow end-to-end with a stub `run_fn` — no QA system needed.

```python
# tests/test_scheduler_integration.py
import asyncio, time
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from scheduler.scheduler import CarbonAwareScheduler, PREvent
from scheduler.carbon_client import CarbonForecast, CarbonWindow
from scheduler.risk_router import RiskBucket


def _make_forecast(intensity: float) -> CarbonForecast:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    win = CarbonWindow(
        zone="eastus",
        start=now + timedelta(hours=2),
        end=now + timedelta(hours=2, minutes=30),
        intensity=80.0,
        is_optimal=True,
    )
    return CarbonForecast(
        zone="eastus",
        current_intensity=intensity,
        windows=[win],
        optimal_window=win,
        is_high_carbon_now=(intensity >= 400),
    )


@pytest.fixture
def run_fn():
    fn = AsyncMock(return_value={"status": "passed", "verdict": "PASS", "duration_s": 1.0})
    return fn


@pytest.fixture
def carbon_client_low(monkeypatch):
    mock = AsyncMock(return_value=_make_forecast(150.0))
    monkeypatch.setattr("scheduler.scheduler.CarbonAwareClient.get_forecast", mock)
    return mock


@pytest.fixture
def carbon_client_high(monkeypatch):
    mock = AsyncMock(return_value=_make_forecast(500.0))
    monkeypatch.setattr("scheduler.scheduler.CarbonAwareClient.get_forecast", mock)
    return mock


@pytest.mark.asyncio
async def test_critical_pr_runs_all_agents_immediately(run_fn, carbon_client_high):
    sched = CarbonAwareScheduler(run_fn=run_fn)
    sched.start()
    decision = await sched.submit(PREvent(pr_id="PR-CRIT", risk_score=0.95))
    sched.stop()

    assert decision.risk_bucket == RiskBucket.CRITICAL
    assert not decision.has_deferred_jobs
    assert "perf_agent" in decision.immediate_agents

@pytest.mark.asyncio
async def test_medium_pr_high_carbon_defers_medium_tier(run_fn, carbon_client_high):
    sched = CarbonAwareScheduler(run_fn=run_fn)
    sched.start()
    decision = await sched.submit(PREvent(pr_id="PR-MED", risk_score=0.55))
    sched.stop()

    assert decision.risk_bucket == RiskBucket.MEDIUM
    assert decision.has_deferred_jobs
    assert "cross_repo_impact_agent" in decision.deferred_agents
    # Cheap agents always run immediately
    assert "api_agent" in decision.immediate_agents

@pytest.mark.asyncio
async def test_low_carbon_no_deferral(run_fn, carbon_client_low):
    sched = CarbonAwareScheduler(run_fn=run_fn)
    sched.start()
    decision = await sched.submit(PREvent(pr_id="PR-LOW", risk_score=0.55))
    sched.stop()

    assert not decision.has_deferred_jobs

@pytest.mark.asyncio
async def test_metrics_increment_on_submit(run_fn, carbon_client_low):
    sched = CarbonAwareScheduler(run_fn=run_fn)
    sched.start()
    await sched.submit(PREvent(pr_id="PR-1", risk_score=0.30))
    await sched.submit(PREvent(pr_id="PR-2", risk_score=0.55))
    sched.stop()

    m = sched.metrics()
    assert m["carbon_scheduler_prs_submitted_total"] == 2

@pytest.mark.asyncio
async def test_cancel_pr_removes_pending_jobs(run_fn, carbon_client_high):
    sched = CarbonAwareScheduler(run_fn=run_fn)
    sched.start()
    await sched.submit(PREvent(pr_id="PR-X", risk_score=0.55))
    cancelled = sched.cancel_pr("PR-X")
    sched.stop()

    assert cancelled >= 1
```

---

## 6. Pipeline Integration Tests (Real QA System)

Run these when `k11techlab-agentic-ai-qa-system` is installed in the same virtual environment.

```bash
# Set up environment
pip install -e ../k11techlab-agentic-ai-qa-system
export QA_PIPELINE_MODULE=k11techlab.runner
export CARBON_SDK_URL=http://localhost:8090   # Carbon Aware SDK running in Docker
```

```python
# tests/integration/test_with_real_pipeline.py
"""
Requires:
  - k11techlab-agentic-ai-qa-system installed (QA_PIPELINE_MODULE=k11techlab.runner)
  - Carbon Aware SDK running at CARBON_SDK_URL (or JSON mock)

Run with:
  pytest tests/integration/ -v --timeout=120
"""
import asyncio, os
import pytest
from scheduler.scheduler import CarbonAwareScheduler, PREvent

pytestmark = pytest.mark.skipif(
    os.getenv("QA_PIPELINE_MODULE") != "k11techlab.runner",
    reason="QA pipeline not configured — set QA_PIPELINE_MODULE=k11techlab.runner",
)


@pytest.mark.asyncio
async def test_cheap_agents_run_and_return_verdict():
    async with CarbonAwareScheduler() as sched:
        decision = await sched.submit(PREvent(
            pr_id="IT-01",
            risk_score=0.20,
            force_full=False,
        ))
    assert "api_agent" in decision.immediate_agents
    # Wait for the immediate job to fire
    await asyncio.sleep(5)


@pytest.mark.asyncio
async def test_full_suite_on_force_full():
    async with CarbonAwareScheduler() as sched:
        decision = await sched.submit(PREvent(
            pr_id="IT-02",
            risk_score=0.20,
            force_full=True,
        ))
    assert "perf_agent" in decision.immediate_agents
    assert not decision.has_deferred_jobs


@pytest.mark.asyncio
async def test_pipeline_result_verdict_is_pass_or_fail():
    """
    Verifies that PipelineResult.verdict is a known value — not None or garbage.
    Catches normalisation bugs in _normalise_result().
    """
    results = []

    async def capturing_run_fn(pr_id, agents, meta):
        from integrations.qa_pipeline_adapter import QAPipelineAdapter
        adapter = QAPipelineAdapter()
        result = await adapter.run_agents(pr_id=pr_id, agents=agents, meta=meta)
        results.append(result)
        return {"status": result.status, "verdict": result.verdict, "duration_s": result.duration_s}

    async with CarbonAwareScheduler(run_fn=capturing_run_fn) as sched:
        await sched.submit(PREvent(pr_id="IT-03", risk_score=0.50))
        await asyncio.sleep(5)

    for r in results:
        assert r.verdict in ("PASS", "FAIL", "HITL", "stub", "error")
```

---

## 7. End-to-End Simulation: 10-PR Carbon Benchmark

Simulate the 180-PR pilot benchmark at small scale to verify carbon savings are measurable.

```python
# tests/integration/test_carbon_benchmark.py
"""
Simulates 10 PRs with varying risk scores against a high-carbon grid.
Asserts:
  - At least 30% of PRs had some deferral applied.
  - Estimated CO2 savings > 0.
  - No CRITICAL PR was deferred.

Run with the Carbon Aware SDK JSON mock for reproducible results.
"""
import asyncio
import pytest
from scheduler.scheduler import CarbonAwareScheduler, PREvent
from scheduler.risk_router import RiskBucket

PR_BATCH = [
    ("PR-B01", 0.10),  # LOW
    ("PR-B02", 0.25),  # LOW
    ("PR-B03", 0.45),  # MEDIUM
    ("PR-B04", 0.55),  # MEDIUM
    ("PR-B05", 0.60),  # MEDIUM
    ("PR-B06", 0.72),  # HIGH
    ("PR-B07", 0.80),  # HIGH
    ("PR-B08", 0.85),  # HIGH
    ("PR-B09", 0.92),  # CRITICAL
    ("PR-B10", 0.99),  # CRITICAL
]


@pytest.mark.asyncio
async def test_10_pr_carbon_benchmark():
    decisions = []
    async with CarbonAwareScheduler() as sched:
        for pr_id, risk in PR_BATCH:
            d = await sched.submit(PREvent(pr_id=pr_id, risk_score=risk))
            decisions.append(d)

    deferred_count = sum(1 for d in decisions if d.has_deferred_jobs)
    critical_decisions = [d for d in decisions if d.risk_bucket == RiskBucket.CRITICAL]
    total_co2_saved = sum(d.estimated_savings_g_co2 for d in decisions)

    # At least 30% of PRs deferred something (on a high-carbon grid)
    assert deferred_count / len(decisions) >= 0.30, (
        f"Deferral rate {deferred_count}/{len(decisions)} below 30%"
    )

    # CRITICAL PRs never deferred
    for d in critical_decisions:
        assert not d.has_deferred_jobs, f"{d.pr_id} is CRITICAL but has deferred jobs"

    # CO2 savings are positive
    assert total_co2_saved > 0.0, "Expected non-zero CO2 savings across batch"

    print(f"\n--- Benchmark Results ---")
    print(f"PRs submitted:    {len(decisions)}")
    print(f"PRs with deferral: {deferred_count}")
    print(f"Total CO2 saved:  {total_co2_saved:.2f} g")
    for d in decisions:
        print(f"  {d.pr_id}  {d.risk_bucket.value:<8}  deferred={d.has_deferred_jobs}  "
              f"saved={d.estimated_savings_g_co2:.2f}g")
```

---

## 8. How to Run All Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio respx

# Unit tests only (no QA system, no SDK)
pytest tests/test_risk_router.py tests/test_cost_model.py tests/test_defer_engine.py -v

# Adapter contract tests
pytest tests/test_qa_pipeline_adapter.py -v

# Scheduler integration (stub pipeline, mock carbon SDK)
pytest tests/test_scheduler_integration.py -v

# Pipeline integration (requires real QA system + Carbon SDK)
export QA_PIPELINE_MODULE=k11techlab.runner
export CARBON_SDK_URL=http://localhost:8090
pytest tests/integration/ -v --timeout=120

# Full benchmark simulation
pytest tests/integration/test_carbon_benchmark.py -v -s
```

### pytest.ini recommended settings

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
log_cli = true
log_cli_level = INFO
```

---

## 9. What Each Test Layer Catches

| Layer | Bugs caught |
|-------|------------|
| Unit (risk router, cost model) | Wrong bucket boundaries, incorrect tier membership, carbon formula errors |
| Unit (defer engine) | Jobs firing after cancellation, timeout cap not applied, job ID collisions |
| Adapter contract | Wrong `agent_tier` string sent to `run_pipeline()`, normalisation of dict vs. dataclass result, timeout not handled |
| Scheduler integration (stub) | `submit()` returning wrong decision shape, metrics not incrementing, cancel not working |
| Pipeline integration (real) | Adapter ↔ QA system version mismatch, `run_pipeline()` signature drift, agent name mismatches |
| End-to-end benchmark | Deferral rate regression, CO₂ savings below expected threshold, CRITICAL PRs being deferred |
