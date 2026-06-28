# Business Requirements Document (BRD)

**Project:** K11tech Carbon-Aware CI Scheduler
**Version:** 1.0
**Date:** 2026-06-24
**Author:** K11 Software Solutions LLC

---

## 1. Executive Summary

Software CI/CD pipelines consume measurable electricity. The carbon cost of that electricity varies by up to 10–20× depending on the time of day and the composition of the regional electricity grid. This project delivers a thin scheduling layer that defers non-urgent CI test workloads to low-carbon grid windows — reducing carbon emissions without compromising developer feedback speed or test coverage quality.

---

## 2. Business Context

### 2.1 Problem Statement

Engineering teams at K11tech run 50–200 CI jobs per day across multiple repositories. These jobs execute regardless of whether the grid is powered primarily by renewable energy or fossil fuels. No mechanism exists to:

- Measure the carbon cost of a CI run at scheduling time.
- Delay non-urgent tests until a cleaner grid window is available.
- Report carbon intensity per PR or per repository for sustainability auditing.

### 2.2 Strategic Drivers

| Driver | Detail |
|--------|--------|
| Sustainability commitments | K11tech's engineering sustainability target: 30% reduction in CI carbon emissions by end of 2026. |
| Regulatory compliance | EU CSRD (effective 2025) requires Scope 2 + Scope 3 emissions reporting, which includes cloud CI compute. |
| SCI instrumentation | Green Software Foundation SCI specification adoption — CI workloads must be measured and attributed. |
| Competitive differentiation | Carbon-aware tooling is a demonstrable engineering practice for enterprise clients in regulated sectors. |

### 2.3 Opportunity

Grid carbon intensity is predictable 8 hours in advance with reasonable accuracy. CI test suites — particularly E2E, performance, and cross-browser tests — can tolerate a 2–4 hour delay without impacting developer workflow. This gap creates a low-friction, high-impact optimisation opportunity.

---

## 3. Stakeholders

| Role | Name / Team | Interest |
|------|-------------|----------|
| Sponsor | K11tech Engineering Leadership | Carbon reduction targets, regulatory compliance |
| Engineering users | Development teams | Uninterrupted fast feedback on PRs |
| QA / Test Engineering | K11tech QA team | Test coverage and quality not degraded |
| Platform / DevOps | CI/CD owners | Minimal integration overhead |
| Legal / Compliance | K11tech Legal | CSRD and SCI reporting artefacts |
| Research | Kavita Jadhav | Academic validation and publication |

---

## 4. Business Requirements

### BR-01 — Carbon Reduction
The system shall reduce the carbon cost of CI test execution by a minimum of 25% compared to always-immediate scheduling, measured over a 180-PR sample period.

### BR-02 — Zero Quality Regression
No test failures, missed defects, or reduction in test coverage shall be attributable to carbon-aware deferral decisions.

### BR-03 — Developer Feedback Latency
Developers shall receive initial PR feedback (fast test tier result) within the same time window as the current baseline — no regression in time-to-first-feedback.

### BR-04 — Configurable Deferral Deadline
Operations teams shall be able to set a maximum deferral horizon (default: 8 hours) beyond which deferred jobs fire unconditionally.

### BR-05 — Risk-Gated Deferral
High-risk and critical PRs shall never have their core test suite deferred. The system must not trade safety for sustainability.

### BR-06 — Observability
Carbon savings, deferral rates, and job statuses shall be exposed as metrics consumable by existing observability tooling (Prometheus-compatible format).

### BR-07 — SCI Instrumentation
The system shall record energy consumed, grid intensity at execution time, and functional unit (per PR) to support SCI formula calculation.

### BR-08 — Minimal Integration Overhead
Integration with an existing QA pipeline shall require no more than one adapter module and one CI configuration change (e.g., a GitHub Actions step).

### BR-09 — Regulatory Reporting Support
The system shall produce per-PR and per-repository carbon cost records suitable for inclusion in CSRD Scope 2/3 reporting.

---

## 5. Constraints

| Constraint | Detail |
|------------|--------|
| Language | Python 3.10+; no JVM or .NET runtime in CI agents |
| Carbon data | Must work with at least one free-tier data provider (no mandatory paid API at MVP) |
| Deferral ceiling | No deferred job may wait longer than 8 hours (configurable) |
| CI compatibility | Must integrate with GitHub Actions without requiring self-hosted runners |
| Backwards compatibility | Existing QA pipeline run_pipeline() interface must remain unchanged |

---

## 6. Success Criteria

| Metric | Target |
|--------|--------|
| Carbon reduction vs. baseline | ≥ 25% (achieved: 31.4% in pilot) |
| Deferred test failures | 0 |
| Median deferred wait | ≤ 4 hours |
| Integration effort | ≤ 1 day for a new pipeline |
| Metrics export | Prometheus-compatible endpoint operational |

---

## 7. Out of Scope

- Carbon-aware scheduling of production traffic or live services.
- Hardware procurement decisions or data-centre energy sourcing.
- Test authoring, test design, or changes to test logic.
- Carbon offsetting or credit purchasing.
- Multi-cloud geographic load shifting (deferred to v2).

---

## 8. Dependencies

| Dependency | Type | Owner |
|------------|------|-------|
| Green Software Foundation Carbon Aware SDK | External OSS | GSF |
| WattTime or Electricity Maps API | External SaaS | WattTime / EM |
| K11tech QA Pipeline (run_agents interface) | Internal | K11tech QA team |
| APScheduler | OSS library | Agronholm / PyPI |
| GitHub Actions webhook | Platform | GitHub |

---

## 9. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Carbon API unavailability | Medium | Low | Fallback to configurable default intensity; never block CI |
| Grid forecast inaccuracy | Low | Low | Deferral decisions use a ±15% buffer on threshold |
| Deferral causes missed SLA | Low | High | SLA deadline parameter hard-caps deferral window |
| QA pipeline interface changes | Medium | Medium | Adapter pattern isolates scheduler from pipeline internals |
| Developer resistance to delayed tests | Medium | Medium | Fast tier always runs immediately; delay is invisible for most PRs |
