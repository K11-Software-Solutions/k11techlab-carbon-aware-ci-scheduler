# Live Production App Test Results

**Status:** PASS_READ_ONLY_PRODUCTION_SMOKE  
**Run date:** 2026-06-28  
**Production base URL:** `https://k11softwaresolutions.com/`  
**Mutation/canary POST:** NOT_RUN; `PROD_ALLOW_MUTATION` was not set.

---

## Production Smoke Run

Command:

```powershell
$env:PROD_BASE_URL = "https://k11softwaresolutions.com/"
python -m pytest tests\production\ -o addopts="" -m "production" -v --tb=short
```

Outcome:

```text
2 passed, 2 skipped in 0.85s
```

Passed:

- `test_production_app_reachable_read_only`
- `test_production_webhook_contract_read_only_probe`

Skipped:

- `test_production_metrics_endpoint_if_available` because `/metrics` is not exposed.
- `test_production_webhook_canary_submit_explicit_opt_in` because `PROD_ALLOW_MUTATION=1` was not set.

---

## Endpoint Status Probe

| Method | Path | Status | Interpretation |
|--------|------|--------|----------------|
| GET | `/health` | 404 | No public health endpoint at this path. |
| GET | `/metrics` | 404 | No public metrics endpoint at this path. |
| GET | `/` | 200 | Company site is reachable. |
| OPTIONS | `/webhook/pr` | 204 | Route/proxy gives a deliberate read-only response to OPTIONS. |

---

## Notes

The first non-escalated run failed with Windows socket permission error `WinError 10013`, so the read-only test was rerun with approved network access.

This result verifies that the provided company URL is reachable and responds safely to the read-only production smoke checks. It does not prove that the carbon-aware scheduler webhook is deployed behind `/webhook/pr`, because the canary POST was intentionally not run.

---

## Optional Next Step: Canary POST

Only run this after confirming a synthetic canary PR is safe in production:

```powershell
$env:PROD_BASE_URL = "https://k11softwaresolutions.com/"
$env:PROD_ALLOW_MUTATION = "1"
$env:PROD_TEST_PR_ID = "prod-smoke-canary"
$env:PROD_TEST_RISK_SCORE = "0.10"
python -m pytest tests\production\test_production_smoke.py::test_production_webhook_canary_submit_explicit_opt_in -o addopts="" -m "production" -v --tb=short
```
---

## How To Add These Endpoints

A FastAPI scheduler API was added in this repo:

```text
api/app.py
```

It exposes:

- `GET /health`
- `GET /metrics`
- `OPTIONS /webhook/pr`
- `POST /webhook/pr`

Local endpoint tests passed:

```text
5 passed in 0.44s
```

Deployment steps are documented in:

```text
artifacts/docs/deploy-production-api-endpoints.md
```