# Why Carbon-Aware Testing?

> A concise explanation of the problem, the opportunity, and why it matters now.

---

## The Hidden Carbon Cost of CI/CD

Every time a pull request triggers a test suite, a fleet of CI runners spins up, executes hundreds of tests, and then shuts down. Each runner draws real electricity. That electricity may come from wind and solar — or from coal and natural gas, depending on the time of day and your cloud region.

Most engineering teams have no idea which.

A typical QA pipeline for a mid-sized software product runs 50–200 CI jobs per day. At 45 W per runner, a 90-second full test suite emits roughly:

```
Energy  = (90 s / 3600) × 45 W × PUE 1.4 = 1.575 Wh = 0.001575 kWh
Carbon  = 0.001575 kWh × grid_intensity (gCO2/kWh)
```

At 400 gCO2/kWh (a dirty coal-heavy grid), that is **0.63 g CO₂ per run**. At 50 gCO2/kWh (a clean wind-heavy grid), it is **0.08 g CO₂** — 8× lower, for the same tests, in the same cloud region, just a few hours apart.

Multiply by 100 runs per day, 250 working days per year, and across dozens of repositories: the total is no longer negligible.

---

## Grid Intensity Varies — A Lot

Real-time grid carbon intensity is published by electricity system operators and aggregated by services like the [Green Software Foundation's Carbon Aware SDK](https://github.com/Green-Software-Foundation/carbon-aware-sdk) and [Electricity Maps](https://www.electricitymaps.com/).

A typical 24-hour cycle in the UK (GB zone) shows swings of **100–450 gCO₂/kWh** — a 4× range — driven by:

- **Renewable availability**: wind output peaks at night and in winter; solar peaks midday in summer.
- **Demand cycles**: grid demand drops overnight; industrial load shapes daytime peaks.
- **Gas peakers**: fossil "peaker" plants fill shortfalls during high-demand hours.

The upshot: if your CI pipeline runs at 09:00 every morning because that is when developers push code, it may consistently land on the dirtiest part of the grid's day.

---

## The Key Insight: Tests Can Wait (Within Limits)

Not all CI work is equally time-sensitive:

| Test type | Latency sensitivity | Deferral potential |
|-----------|--------------------|--------------------|
| Unit tests, contract checks | High — blocks the PR | None |
| Fast integration tests | High | None |
| E2E browser tests | Medium — hours are fine | **High** |
| Cross-browser visual regression | Low — overnight is fine | **Very high** |
| Performance / load tests | Low | **Very high** |

The carbon-aware scheduler exploits this asymmetry. Cheap, fast tests run immediately. Expensive, slower tests are deferred to the next low-carbon window — typically within 2–3 hours, never beyond the configurable maximum (default: 8 hours).

The developer still gets fast feedback from the cheap tier. The expensive tests complete before the next working day. Carbon is saved; quality is preserved.

---

## Why CI/CD, Not Production Workloads?

Carbon-aware scheduling of production services requires load-shedding decisions, capacity planning, and SLA renegotiation — hard problems with real customer impact.

CI workloads are different:

1. **Naturally bursty**: CI jobs already queue; a 2-hour delay is indistinguishable from queue wait.
2. **No external SLA**: the only stakeholder is the engineering team.
3. **Predictable duration**: agent runtimes are stable enough to estimate carbon cost in advance.
4. **No hardware investment**: the scheduler runs as a thin adapter in front of an existing QA pipeline.

This makes CI the lowest-friction entry point for software carbon reduction — real savings with near-zero adoption cost.

---

## The Role of Risk Routing

Blindly deferring all tests would break the feedback loop that makes CI valuable. The scheduler avoids this by coupling deferral to a **QA risk score** for each pull request:

- **Low-risk PRs** (documentation, config tweaks): most tests deferred — little risk.
- **Medium-risk PRs** (feature additions): cheap tier runs immediately; medium tier defers.
- **High-risk PRs** (auth, data migrations): almost everything runs immediately; only performance tests defer.
- **Critical PRs** (risk ≥ 0.90): full suite runs immediately regardless of carbon.

Risk is the guard that ensures carbon optimisation never compromises safety.

---

## Regulatory Pressure Is Growing

Carbon-aware scheduling is no longer just an engineering curiosity. Two regulatory frameworks are converging to make software carbon accounting a compliance requirement:

### EU Corporate Sustainability Reporting Directive (CSRD)

Effective for large companies from 2025, the CSRD requires Scope 1, 2, and **Scope 3** emissions reporting. CI compute — whether in-house or cloud — falls under Scope 2 (direct cloud energy) or Scope 3 (purchased cloud services). Companies that cannot measure it cannot report it.

### Green Software Foundation — Software Carbon Intensity (SCI) Specification

The [SCI specification](https://sci-guide.greensoftware.foundation/) defines a standardised formula for software carbon intensity:

```
SCI = (E × I + M) / R
```

Where:
- `E` = energy consumed by the software
- `I` = marginal carbon intensity of the electricity grid
- `M` = embodied carbon of the hardware
- `R` = functional unit (e.g., per test run, per PR, per deploy)

This scheduler instruments all four components for CI workloads, making SCI reporting tractable for engineering teams.

---

## Measured Impact

From 180 PRs of K11tech pipeline telemetry:

| Metric | Result |
|--------|--------|
| Carbon reduction vs. always-immediate scheduling | **31.4 %** |
| Test failures introduced by deferral | **0** |
| Median deferred wait time | **2.1 hours** |
| PRs where carbon deferral was applied | **~38 %** |

These results were achieved with a single Python module added to an existing QA pipeline — no infrastructure changes, no test rewrites, no hardware.

---

## Further Reading

- [Green Software Foundation — Carbon Aware SDK](https://github.com/Green-Software-Foundation/carbon-aware-sdk)
- [SCI Specification v1.0](https://sci-guide.greensoftware.foundation/)
- [Electricity Maps — Real-Time Carbon Intensity](https://www.electricitymaps.com/)
- [EU CSRD Overview (European Commission)](https://finance.ec.europa.eu/capital-markets-union-and-financial-markets/company-reporting-and-auditing/company-reporting/corporate-sustainability-reporting_en)
- Jadhav, K. (2026). *Carbon-Aware Test Scheduling in Agentic CI/CD Pipelines.* K11 Software Solutions LLC.
