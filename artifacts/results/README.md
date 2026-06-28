# Empirical Evaluation Results

This folder stores test run outputs and summaries for the research paper's empirical evaluation section.

## Files

| File | Purpose |
|------|---------|
| `empirical-evaluation-summary.csv` | Phase-level quantitative summary suitable for tables. |
| `empirical-evaluation-results.json` | Structured machine-readable results with environment, commands, outcomes, and limitations. |
| `empirical-evaluation-results.md` | Human-readable result narrative for paper drafting. |
| `phase-4-docker-run-summary.md` | Focused Phase 4 Docker run summary with setup observations. |
| `phase-5-carbon-accounting-summary.md` | Focused Phase 5 carbon accounting and metrics validation summary. |
| `live-production-app-test-results.md` | Production smoke-test harness status and pending production URL inputs. |
| `live-production-app-test-results.json` | Machine-readable production smoke-test harness status. |
| `separate-production-app-results.md` | Dedicated production API app scaffold and local smoke-test evidence. |
| `separate-production-app-results.json` | Machine-readable standalone production app verification result. |

## Provenance

Results were captured from local pytest runs on 2026-06-24, 2026-06-25, and 2026-06-28 in:

```text
C:\Users\kavit\Automation\K11tech\k11techlab-carbon-aware-ci-scheduler
```

The detailed operational log is stored in:

```text
artifacts/docs/phase-4-step-by-step-test-run-log.md
```

## Interpretation

Phases 1 through 5 passed. The standalone production API app contract also passed local verification. Coverage was not measured because the executed strategy commands did not include `--cov`.

