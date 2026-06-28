# Separate Production App Results

**Run date:** 2026-06-28  
**Status:** `LOCAL_REPO_CREATED_DEPLOYMENT_PENDING`  
**Standalone app path:** `production-scheduler-api-app/`  
**Local Git commit:** `e30fcc0`

## Purpose

Create a dedicated deployable API app for live production testing instead of using the company website.

This separates marketing-site behavior from scheduler API behavior and gives the empirical evaluation a controlled production target.

## Endpoints Implemented

| Method | Path | Expected status |
|---|---|---|
| `GET` | `/health` | `200` |
| `GET` | `/metrics` | `200` or protected with `401` when token is set |
| `OPTIONS` | `/webhook/pr` | `204` |
| `POST` | `/webhook/pr` | `202` or protected with `401` when token is set |

## Verification Runs

### Standalone app tests

```powershell
cd production-scheduler-api-app
python -m pytest -v
```

Result:

```text
5 passed in 0.39s
```

### Production smoke harness against local standalone app

The app was started locally on `http://127.0.0.1:8017`, then the existing production smoke suite was run with mutation explicitly enabled because the target was the disposable standalone app.

```powershell
$env:PROD_BASE_URL = "http://127.0.0.1:8017"
$env:PROD_ALLOW_MUTATION = "1"
$env:PROD_TEST_PR_ID = "local-standalone-prod-canary"
$env:PROD_TEST_RISK_SCORE = "0.10"
python -m pytest tests\production\ -o addopts="" -m "production" -v --tb=short
```

Result:

```text
4 passed in 0.23s
```

Passed tests:

- `test_production_app_reachable_read_only`
- `test_production_metrics_endpoint_if_available`
- `test_production_webhook_contract_read_only_probe`
- `test_production_webhook_canary_submit_explicit_opt_in`

## Deployment Status

The app is ready to push to a dedicated GitHub repository and deploy with Docker. The actual public deployment is still pending because a cloud target and account authorization are required.

Recommended next step:

```powershell
cd production-scheduler-api-app
gh repo create k11-carbon-scheduler-api --private --source . --remote origin --push
```

Then deploy the repo as a Docker web service and rerun the production smoke suite with the deployed URL.

## Interpretation For Empirical Evaluation

The local result validates that the dedicated app implements the production-test contract required by the research harness. It does not yet demonstrate external Internet reachability, deployment-platform behavior, DNS, or TLS, because the app has not been deployed to a public production URL.
