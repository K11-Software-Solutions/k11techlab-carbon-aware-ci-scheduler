# Live Production App Testing Guide

**Project:** K11tech Carbon-Aware CI Scheduler  
**Purpose:** Safely test a deployed production scheduler app without accidentally triggering destructive production behavior.  
**Status:** Test harness added; execution requires a real `PROD_BASE_URL`.  
**Deployment guide:** [deploy-production-api-endpoints.md](./deploy-production-api-endpoints.md)

---

## Safety Model

Production testing must be more conservative than local Docker or staging E2E tests.

The production suite in `tests/production/test_production_smoke.py` follows three rules:

1. It skips unless `PROD_BASE_URL` is explicitly set.
2. It is read-only by default.
3. The webhook POST canary is skipped unless `PROD_ALLOW_MUTATION=1` is explicitly set.

This prevents accidental PR scheduling, deferred jobs, or QA pipeline execution against a live production system.

---

## What The Production Suite Checks

| Test | Default behavior | Purpose |
|------|------------------|---------|
| `test_production_app_reachable_read_only` | GET only | Verifies that at least one configured smoke path is reachable. |
| `test_production_metrics_endpoint_if_available` | GET only; skips on 404/protected endpoint | Verifies metrics are exposed if production publishes them. |
| `test_production_webhook_contract_read_only_probe` | OPTIONS only | Verifies the webhook route/proxy responds deliberately without submitting a PR. |
| `test_production_webhook_canary_submit_explicit_opt_in` | Skipped unless `PROD_ALLOW_MUTATION=1` | Optional canary POST to the production webhook. |

---

## Required Environment

Set at minimum:

```powershell
$env:PROD_BASE_URL = "https://your-production-scheduler.example.com"
```

Optional settings:

```powershell
$env:PROD_AUTH_TOKEN = "<bearer-token-if-required>"
$env:PROD_SMOKE_PATHS = "/health,/metrics,/"
$env:PROD_METRICS_PATH = "/metrics"
$env:PROD_WEBHOOK_PATH = "/webhook/pr"
$env:PROD_TIMEOUT_SECONDS = "10"
```

Optional webhook canary settings:

```powershell
$env:PROD_ALLOW_MUTATION = "1"
$env:PROD_TEST_PR_ID = "prod-smoke-canary"
$env:PROD_TEST_RISK_SCORE = "0.10"
```

Only enable `PROD_ALLOW_MUTATION=1` after confirming the production app treats the canary PR safely.

---

## Run Read-Only Production Smoke Tests

```powershell
python -m pytest tests\production\ -o addopts="" -m "production" -v --tb=short
```

Expected behavior without `PROD_BASE_URL`:

```text
skipped
```

Expected behavior with a reachable production URL:

```text
passed or skipped for protected/optional endpoints
```

---

## Run Optional Webhook Canary

Use this only when a canary submission is approved:

```powershell
$env:PROD_ALLOW_MUTATION = "1"
$env:PROD_TEST_PR_ID = "prod-smoke-canary"
$env:PROD_TEST_RISK_SCORE = "0.10"

python -m pytest tests\production\test_production_smoke.py::test_production_webhook_canary_submit_explicit_opt_in -o addopts="" -m "production" -v --tb=short
```

The payload is intentionally low risk:

```json
{
  "pr_id": "prod-smoke-canary",
  "risk_score": 0.10,
  "force_full": false,
  "metadata": {
    "source": "production_smoke_test",
    "expected_side_effect": "canary_scheduler_submission"
  }
}
```

---

## Result Recording

Record production test outcomes in:

```text
artifacts/results/live-production-app-test-results.md
artifacts/results/live-production-app-test-results.json
```

The initial result files currently mark the run as `NOT_RUN` because no production URL has been provided.

---

## Important Boundaries

Do not run load tests against production from this suite.

Do not use real customer PR IDs for canary testing.

Do not enable the mutation canary unless the team confirms that a synthetic PR ID is safe in production.

For empirical research reporting, distinguish clearly between:

- Docker-hosted live SDK testing from Phase 4.
- Live production app smoke testing.
- Any future production canary POST that actually schedules work.

