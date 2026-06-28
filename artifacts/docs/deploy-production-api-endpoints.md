# Deploy Production API Endpoints

**Purpose:** Add live-testable production endpoints for the carbon-aware scheduler.

---

## Recommended Architecture

Do not put scheduler API routes on the marketing homepage unless that site is already your backend app.

Recommended split:

```text
https://k11softwaresolutions.com             -> company website
https://scheduler.k11softwaresolutions.com   -> carbon-aware scheduler API
```

The scheduler API should expose:

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Read-only uptime/status check. |
| `GET /metrics` | Prometheus-style scheduler metrics. |
| `OPTIONS /webhook/pr` | Read-only route/proxy contract probe. |
| `POST /webhook/pr` | PR scheduling webhook. Mutating endpoint; protect with auth. |

---

## What Was Added In This Repo

FastAPI app:

```text
api/app.py
```

Tests:

```text
tests/test_api_app.py
```

Local verification:

```text
5 passed in 0.44s
```

---

## Run Locally

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Start the API:

```powershell
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

Try the endpoints:

```powershell
Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
Invoke-WebRequest http://localhost:8000/metrics -UseBasicParsing
Invoke-WebRequest http://localhost:8000/webhook/pr -Method OPTIONS -UseBasicParsing
```

Submit a canary PR locally:

```powershell
Invoke-WebRequest `
  -Uri http://localhost:8000/webhook/pr `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"pr_id":"local-canary","risk_score":0.10,"metadata":{"source":"local"}}' `
  -UseBasicParsing
```

---

## Protect Production With A Bearer Token

Set this in production:

```powershell
$env:SCHEDULER_API_TOKEN = "<strong-random-token>"
```

Then call protected endpoints with:

```powershell
Authorization: Bearer <strong-random-token>
```

`/health` is intentionally public. `/metrics` and `POST /webhook/pr` are protected when `SCHEDULER_API_TOKEN` is set.

---

## Production Smoke Test

After deployment:

```powershell
$env:PROD_BASE_URL = "https://scheduler.k11softwaresolutions.com"
$env:PROD_AUTH_TOKEN = "<token-if-required>"
python -m pytest tests\production\ -o addopts="" -m "production" -v --tb=short
```

Only run the webhook canary after approval:

```powershell
$env:PROD_ALLOW_MUTATION = "1"
$env:PROD_TEST_PR_ID = "prod-smoke-canary"
$env:PROD_TEST_RISK_SCORE = "0.10"
python -m pytest tests\production\test_production_smoke.py::test_production_webhook_canary_submit_explicit_opt_in -o addopts="" -m "production" -v --tb=short
```

---

## Minimal Deployment Checklist

1. Deploy this repo as a Python service.
2. Run `uvicorn api.app:app --host 0.0.0.0 --port $PORT`.
3. Set `CARBON_SDK_BASE_URL`.
4. Set `CARBON_SDK_ZONE`.
5. Set `QA_PIPELINE_MODULE`.
6. Set `SCHEDULER_API_TOKEN`.
7. Point DNS such as `scheduler.k11softwaresolutions.com` to the service.
8. Run read-only production smoke tests.
9. Run the canary POST only after confirming it is safe.

