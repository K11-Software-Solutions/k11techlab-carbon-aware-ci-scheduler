# Separate Production App Repo

**Purpose:** Create a dedicated deployable API app for live production testing without using the company website as the test target.

## Local App Created

```text
production-scheduler-api-app/
```

The app exposes:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Public production health check. |
| `GET` | `/metrics` | Prometheus-style scheduler metrics. |
| `OPTIONS` | `/webhook/pr` | Safe read-only webhook route probe. |
| `POST` | `/webhook/pr` | Synthetic PR scheduling canary. |

## Why This Is Better Than Testing The Company Website

The company website can stay focused on marketing and customer traffic. The production smoke suite can target a small API service whose only job is to expose scheduler-compatible endpoints for empirical evaluation.

This gives the research paper a cleaner boundary:

1. Website availability is not mixed with scheduler API behavior.
2. The test harness can safely run canary requests against a dedicated service.
3. The app can be deployed, versioned, and rolled back independently.
4. Results from `/health`, `/metrics`, and `/webhook/pr` map directly to the empirical evaluation section.

## Create A Real GitHub Repo

From this workspace:

```powershell
cd production-scheduler-api-app
git init
git add .
git commit -m "Add standalone scheduler production API"
gh repo create k11-carbon-scheduler-api --private --source . --remote origin --push
```

Use `--public` instead of `--private` if you want the deployment platform to connect without private-repo permissions.

## Deploy

Render is the easiest path for this app because `render.yaml` and `Dockerfile` are included.

1. Push `production-scheduler-api-app/` to its own GitHub repo.
2. In Render, create a new Web Service from that repo.
3. Use Docker deployment.
4. Set health check path to `/health`.
5. Set `APP_ENV=production`.
6. Set `SERVICE_NAME=k11-carbon-aware-scheduler-api`.
7. Optionally set `SCHEDULER_API_TOKEN=<strong-token>`.
8. Deploy and copy the public service URL.

## Test After Deployment

From the main research repo:

```powershell
$env:PROD_BASE_URL = "https://<deployed-scheduler-api-url>"
$env:PROD_AUTH_TOKEN = "<token-if-required>"
python -m pytest tests\production\ -o addopts="" -m "production" -v --tb=short
```

Run the canary POST only after confirming the app is dedicated to test submissions:

```powershell
$env:PROD_ALLOW_MUTATION = "1"
$env:PROD_TEST_PR_ID = "prod-smoke-canary"
$env:PROD_TEST_RISK_SCORE = "0.10"
python -m pytest tests\production\test_production_smoke.py::test_production_webhook_canary_submit_explicit_opt_in -o addopts="" -m "production" -v --tb=short
```

## Evidence To Store

Store deployment and test results in:

```text
artifacts/results/separate-production-app-results.md
artifacts/results/separate-production-app-results.json
```

Record:

1. Deployment platform and app URL.
2. Commit SHA deployed.
3. `/health` status.
4. `/metrics` status.
5. `OPTIONS /webhook/pr` status.
6. Canary `POST /webhook/pr` status, if enabled.
7. Full pytest summary.
