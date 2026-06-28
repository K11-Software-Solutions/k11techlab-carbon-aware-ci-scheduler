# Role in an Agentic AI QA System

> How the Carbon-Aware CI Scheduler functions as the orchestration and dispatch layer of an agentic AI quality assurance pipeline.

---

## 1. The Agents Are AI Agents

The scheduler does not manage generic test scripts. The units it dispatches — defined in `cost_model.py` — are autonomous AI agents, each responsible for a specific quality dimension of a pull request:

| Agent | Intelligence Layer | Quality Domain |
|-------|--------------------|----------------|
| `api_agent` | OpenAPI diff + Claude Haiku LLM call | Contract validation |
| `security_agent` | SAST pattern matching + LLM triage | Security posture |
| `data_agent` | Schema migration diff + integrity checks | Data safety |
| `a11y_agent` | axe-core WCAG evaluation (headless) | Accessibility |
| `regression_agent` | Pytest unit suite (fast subset) | Functional regression |
| `cross_repo_impact_agent` | Contract Registry MCP queries + NetworkX graph traversal | Cross-service impact |
| `drift_analysis_agent` | Contract change velocity computation | API drift detection |
| `playwright_agent` | Playwright E2E suite (Chromium + Firefox + WebKit) | End-to-end correctness |
| `perf_agent` | k6 load test + Lighthouse audit | Performance |
| `browser_agent` | Cross-browser visual regression | UI consistency |

Each agent reasons about the PR independently using tools, LLMs, or both — the defining characteristic of an agentic system.

---

## 2. Where This Project Sits in the Agentic Pipeline

```
Pull Request Opened
         │
         ▼
 ┌───────────────────┐
 │  Risk Scoring     │  ← Upstream AI agent: LLM analyses the diff,
 │  Agent            │    assigns risk_score ∈ [0.0, 1.0]
 └────────┬──────────┘
          │  risk_score + pr_id + zone
          ▼
 ┌─────────────────────────────────────────────────┐
 │         Carbon-Aware CI Scheduler               │  ← THIS PROJECT
 │                                                 │
 │  CarbonAwareClient → grid forecast              │
 │  RiskRouter        → which agents, when         │
 │  DeferEngine       → schedules immediate jobs   │
 │                      schedules deferred jobs    │
 └────────┬────────────────────┬────────────────────┘
          │ immediate           │ deferred (low-carbon window)
          ▼                     ▼
  api_agent               perf_agent
  security_agent          browser_agent
  data_agent              playwright_agent (HIGH risk only)
  a11y_agent
  regression_agent
  cross_repo_impact_agent
  drift_analysis_agent
          │
          ▼
  Results aggregated → PR feedback → Developer
```

The scheduler is the **orchestration layer**: it decides which agents activate, in what sequence, and at what time — driven by two signals that a naive CI trigger ignores: PR risk and grid carbon intensity.

---

## 3. Agentic Roles This Project Fulfils

### 3.1 Planner

The `RiskRouter` is a planning component. Given a risk score and a carbon forecast, it produces a plan: which agents run now, which wait, and why. This matches the Plan step in a classic Reason–Act–Observe agentic loop.

```python
decision = router.route(risk_score=0.65, forecast=forecast)
# → immediate: [api_agent, security_agent, data_agent, a11y_agent, regression_agent]
# → deferred:  [cross_repo_impact_agent, drift_analysis_agent]
# → reason:    "MEDIUM risk + high carbon (450 gCO2/kWh) — deferring medium-tier agents"
```

### 3.2 Resource-Aware Dispatcher

The `DeferEngine` executes the plan. It separates agent execution into two time-shifted batches: one that fires within seconds (fast feedback to the developer) and one that fires at the next clean energy window (expensive AI calls that can wait). This is resource-aware dispatch — something a simple CI `run all tests` command cannot do.

### 3.3 Cost Model Owner

`cost_model.py` gives the system a quantified understanding of each agent's compute weight:

```
cost_score = 0.5 × normalised_duration + 0.3 × normalised_memory + 0.2 × gpu_flag
```

This is how an agentic orchestrator decides trade-offs. Without a cost model, all agents look equal. With one, the scheduler can calculate the carbon cost of running a specific agent set *right now* versus *in three hours* and make the greener choice automatically.

### 3.4 Feedback Loop Provider

`scheduler.metrics()` emits Prometheus-compatible counters that close the observability loop for the broader agentic system:

```json
{
  "carbon_scheduler_prs_submitted_total": 45,
  "carbon_scheduler_deferred_jobs_total": 17,
  "carbon_scheduler_co2_saved_grams_total": 214.3,
  "carbon_scheduler_jobs_pending": 3
}
```

These metrics allow the system — or a human overseeing it — to observe outcomes, detect drift (e.g., deferral rate dropping), and adjust thresholds. That is the Observe step of the agentic loop.

---

## 4. How Risk Score Drives Agent Selection

The risk score is the primary signal the agentic pipeline uses to decide the depth of QA:

```
risk_score < 0.40  →  CHEAP agents only
                       (5 fast AI agents, ~5 s combined)

risk_score 0.40–0.69  →  CHEAP + MEDIUM agents
                           (7 agents, ~10 s; cross-repo impact included)

risk_score 0.70–0.89  →  CHEAP + MEDIUM + playwright (immediate)
                           perf_agent + browser_agent deferred if carbon is high

risk_score ≥ 0.90  →  ALL agents, immediately, no deferral
                        (Safety overrides sustainability)
```

This graduated response mirrors how a human senior QA engineer would triage a PR: spend proportional effort based on the assessed risk, not a fixed checklist for every change.

---

## 5. Carbon Awareness as a System-Level Constraint

Without carbon awareness, an agentic QA system treats all compute as free and interchangeable. Every agent fires on every event regardless of when or how expensive it is to run right now. This is sustainable only at small scale.

At K11tech scale (50–200 CI events/day across multiple repositories), the AI agents — especially LLM-backed ones — represent a non-trivial electricity load. The carbon intensity of that load varies by up to 10–20× across a 24-hour period.

The scheduler injects carbon intensity as a first-class scheduling constraint:

```
Should we run perf_agent now?
  → PR risk is MEDIUM (0.55)       → perf_agent is not required immediately
  → Current grid: 450 gCO₂/kWh    → defer
  → Optimal window: 3h from now at 90 gCO₂/kWh
  → Estimated saving: 12.4 g CO₂
  → Schedule deferred job
```

This makes the agentic QA system environmentally accountable without removing any quality signal — the agent still runs, just at a better time.

---

## 6. SCI Instrumentation for Agentic Workloads

The Green Software Foundation's Software Carbon Intensity (SCI) formula applies directly to AI agent workloads:

```
SCI = (E × I + M) / R

E = energy consumed by the agent run (kWh)
I = grid carbon intensity at execution time (gCO₂/kWh)
M = embodied carbon of the CI runner hardware
R = functional unit = one PR analysed
```

This scheduler records `E × I` for every agent batch fired — both immediate and deferred — enabling per-PR SCI reporting. For organisations under CSRD reporting obligations, this turns each agentic QA run into a measurable, auditable carbon event rather than an opaque compute black box.

---

## 7. What the Agentic QA System Gains

| Capability | Without This Project | With This Project |
|------------|---------------------|------------------|
| Agent selection | All agents run on every PR | Agents selected by risk score |
| Execution timing | Fires immediately, always | Immediate for fast agents; deferred for expensive ones |
| Carbon cost | Unknown, unmanaged | Measured and optimised per PR |
| Developer feedback speed | Full suite blocks fast feedback | Fast tier runs first; no speed regression |
| Observability | CI logs only | Prometheus metrics: CO₂ saved, deferral rate, pending jobs |
| Regulatory reporting | No carbon data | Per-PR SCI data for CSRD Scope 2/3 |
| Safety guard | No override for critical changes | CRITICAL PRs always run full suite immediately |

---

## Further Reading

- [why-carbon-aware-testing.md](why-carbon-aware-testing.md) — The case for carbon-aware CI and measured impact data
- [carbon-aware-sdk-guide.md](carbon-aware-sdk-guide.md) — How the Carbon Aware SDK is used to fetch grid forecasts
- [functional-requirements-document.md](functional-requirements-document.md) — Full module and interface specifications
- Jadhav, K. (2026). *Carbon-Aware Test Scheduling in Agentic CI/CD Pipelines.* K11 Software Solutions LLC.
