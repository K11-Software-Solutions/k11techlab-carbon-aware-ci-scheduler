# Empirical Evaluation Results

**Evaluation dates:** 2026-06-24 to 2026-06-25  
**System under test:** K11tech Carbon-Aware CI Scheduler  
**Result:** Phases 1 through 5 passed.

---

## Quantitative Summary

| Phase | Layer | Selected tests | Passed | Failed | Errors | Duration |
|-------|-------|----------------|--------|--------|--------|----------|
| Phase 1 | Unit | 130 | 130 | 0 | 0 | 30.08s |
| Phase 2 | Integration stub | 26 | 26 | 0 | 0 | 4.35s |
| Phase 3 | Integration agentic | 9 | 9 | 0 | 0 | 3.50s |
| Phase 4 | E2E Docker | 12 | 12 | 0 | 0 | 4.94s |
| Phase 5 | Carbon accounting | 20 | 20 | 0 | 0 | 7.15s |

Total phase-level selected tests: 197  
Total phase-level passed tests: 197  
Observed failures: 0  
Observed errors after setup fixes: 0

---

## Empirical Claims Supported

1. The risk router produced correct routing behavior across the planned LOW, MEDIUM, HIGH, and CRITICAL scenarios in unit and stub integration tests.
2. The scheduler successfully executed the submit-to-route-to-defer chain with a stub QA pipeline.
3. The QA adapter integrated with the real agentic QA pipeline in memory mode.
4. The Phase 4 E2E suite executed with a Docker-hosted Carbon Aware SDK WebAPI and the two real QA repositories available on `PYTHONPATH`.
5. The clean-diff quality-regression checks produced no FAIL verdicts.
6. Metrics and CO2 savings smoke checks passed.
7. Phase 5 carbon accounting tests verified savings formulas, Prometheus metric key shape, DeferEngine accumulation, threshold boundaries, SCI linearity, and gram-level units.

---

## Phase 4 Docker Finding

The live Docker setup required additional SDK configuration beyond the original strategy command.

Working setup:

```text
Image: ghcr.io/green-software-foundation/carbon-aware-sdk:latest
Port mapping: 8090:8080
Datasource: JSON emissions datasource
Readiness endpoint: HTTP 204 with empty body
```

Interpretation:

The test validated live SDK WebAPI availability and the scheduler's conservative fallback handling. It did not validate live WattTime or Electricity Maps provider data.

---

## Limitations

Coverage was not measured because the executed commands did not include `--cov`.

Phase 4 used a local JSON emissions datasource rather than an external grid provider. Forecast data was not available through the JSON datasource in the SDK image used during validation.
---

## Separate Production App Verification

On 2026-06-28, a dedicated deployable production API app was created in:

```text
production-scheduler-api-app/
```

Local verification passed:

| Scope | Selected tests | Passed | Failed | Duration |
|-------|----------------|--------|--------|----------|
| Standalone app endpoint tests | 5 | 5 | 0 | 0.39s |
| Production smoke harness against local standalone app | 4 | 4 | 0 | 0.23s |

The app is ready to push to a separate GitHub repository and deploy as a Docker web service. Public cloud deployment is still pending platform/account authorization.
