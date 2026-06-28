# Phase 4 Step-by-Step Test Run Log

**Project:** K11tech Carbon-Aware CI Scheduler  
**Run date:** 2026-06-25  
**Purpose:** Explain the Phase 4 live Docker test run for readers who have not used the Carbon Aware SDK or the agentic QA system before.  
**Results folder:** [../results](../results)

---

## 1. Plain-English System Overview

Phase 4 tests the scheduler in a deployed-style setup.

| Component | What it does in this run |
|-----------|--------------------------|
| Carbon Aware SDK | Runs in Docker and exposes an HTTP API at `http://localhost:8090`. The scheduler asks it for grid carbon intensity. |
| Carbon-aware scheduler | Decides which QA agents run immediately and which can be deferred based on PR risk and carbon intensity. |
| Agentic QA system | Provides the real QA pipeline entry point, `pipeline.runner`, used by the adapter. |
| Microservice QA system | Acts as the target repository for system-level QA integration checks. |
| Pytest E2E suite | Runs the actual Phase 4 checks in `tests/e2e/test_carbon_scheduling_e2e.py`. |

The basic flow is:

```text
pytest
  -> CarbonAwareScheduler
  -> CarbonAwareClient
  -> Carbon Aware SDK Docker API
  -> RiskRouter
  -> QAPipelineAdapter
  -> agentic QA pipeline
```

---

## 2. Initial Test Goal

The intended Phase 4 command from the strategy was:

```bash
pytest tests/e2e/ --e2e --slow -v --tb=long
```

Two adjustments were needed in this repository:

1. `--e2e` and `--slow` are documented as flags, but they are not registered pytest CLI options.
2. `pytest.ini` excludes `e2e` and `slow` tests by default through `addopts`.

The working pytest command was:

```powershell
python -m pytest tests\e2e\ -o addopts="" -m "e2e and slow" -v --tb=long
```

---

## 3. Prerequisite Check

The two sibling QA repositories were present:

```text
../k11techlab-agentic-ai-qa-system
../k11techlab-microservice-qa-system
```

Docker was installed but not initially running. The first Docker check failed because the Docker Desktop Linux engine pipe was unavailable.

Observed issue:

```text
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

Docker Desktop was launched:

```powershell
Start-Process -FilePath 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -WindowStyle Hidden
```

Readiness was confirmed with:

```powershell
docker version
```

The command returned both Client and Server sections, confirming Docker Desktop was ready.

---

## 4. Carbon SDK Container Setup Attempts

### Attempt 1: Strategy Command

Command:

```powershell
docker run -d --rm --name carbon-aware-sdk-phase4 -p 8090:8090 ghcr.io/green-software-foundation/carbon-aware-sdk:latest
```

Result:

The image pulled successfully, but the container exited.

Log:

```text
Unhandled exception. CarbonAware.Exceptions.ConfigurationException: No data sources are configured
```

Interpretation:

The SDK image requires a configured datasource. The bare container is not enough.

### Attempt 2: API Image from Local Guide

Command:

```powershell
docker run -d --name carbon-aware-sdk-phase4-json -p 8090:80 ghcr.io/green-software-foundation/carbon-aware-sdk-api:latest
```

Result:

```text
docker: Error response from daemon: error from registry: denied
```

Interpretation:

The `carbon-aware-sdk-api` image was not pullable in this environment.

### Attempt 3: Available SDK Image with JSON Datasource

Image inspection showed the available image listens on container port `8080`:

```text
ASPNETCORE_HTTP_PORTS=8080
```

The port mapping was changed from `8090:8090` to `8090:8080`.

The JSON datasource was configured. The first version configured both emissions and forecast JSON:

```powershell
-e DataSources__EmissionsDataSource=Json
-e DataSources__ForecastDataSource=Json
```

Result:

```text
JSON data source is not supported for forecast data
```

Interpretation:

The JSON datasource can be used for emissions data in this SDK image, but not forecast data.

### Attempt 4: Emissions-Only JSON Datasource

The local JSON datasource was mounted into the container. The first try pointed `DataFileLocation` to a directory.

Result:

```text
Permission denied
```

Interpretation:

The SDK expected a file path, not the directory path.

### Final Working SDK Setup

A local JSON emissions file was generated:

```text
.tmp/phase4-carbon-json/eastus.json
```

The working container command pointed directly to that file:

```powershell
docker run -d `
  --name carbon-aware-sdk-phase4-file `
  -p 8090:8080 `
  -v "${dataDir}:/app/data-sources/json:ro" `
  -e DataSources__EmissionsDataSource=Json `
  -e DataSources__Configurations__Json__Type=Json `
  -e DataSources__Configurations__Json__DataFileLocation=/app/data-sources/json/eastus.json `
  -e DataSources__Configurations__Json__CacheJsonData=true `
  ghcr.io/green-software-foundation/carbon-aware-sdk:latest
```

The container stayed running:

```text
carbon-aware-sdk-phase4-file Up ... 0.0.0.0:8090->8080/tcp
```

Endpoint probe:

```powershell
Invoke-WebRequest `
  -Uri http://localhost:8090/emissions/bylocations/best?location=eastus `
  -UseBasicParsing `
  -TimeoutSec 10 |
  Select-Object StatusCode,Content
```

Observed response:

```text
StatusCode: 204
Content: {}
```

Interpretation:

The live SDK WebAPI accepted the request, but returned no body for the current instant. The scheduler client handled this through its conservative fallback behavior, and the E2E suite permits empty forecast windows.

---

## 5. Environment Variables Used

Before running tests:

```powershell
$env:PYTHONPATH = "..\k11techlab-agentic-ai-qa-system;..\k11techlab-microservice-qa-system;$env:PYTHONPATH"
$env:QA_PIPELINE_MODULE = "pipeline.runner"
$env:CARBON_SDK_BASE_URL = "http://localhost:8090"
$env:CARBON_SDK_ZONE = "eastus"
$env:CARBON_HIGH_THRESHOLD = "400"
```

Meaning:

| Variable | Purpose |
|----------|---------|
| `PYTHONPATH` | Lets the scheduler import the sibling agentic and microservice QA repositories. |
| `QA_PIPELINE_MODULE` | Tells `QAPipelineAdapter` to load `pipeline.runner`. |
| `CARBON_SDK_BASE_URL` | Points `CarbonAwareClient` to the Docker SDK API. |
| `CARBON_SDK_ZONE` | Uses `eastus` as the test grid zone. |
| `CARBON_HIGH_THRESHOLD` | Treats intensity at or above `400` as high carbon. |

---

## 6. Final Phase 4 Test Command

```powershell
python -m pytest tests\e2e\ -o addopts="" -m "e2e and slow" -v --tb=long
```

Final result:

```text
12 passed in 4.94s
```

Test groups that passed:

| Test group | Passed checks |
|------------|---------------|
| `TestCarbonAwareRoutingE2E` | SDK intensity fetch, forecast handling, LOW/HIGH/CRITICAL routing. |
| `TestNoQualityRegressionE2E` | Cheap tier and deferred/full tier verdict compatibility. |
| `TestCarbonSavingsMeasurementE2E` | Savings non-negativity, metrics counter behavior, pending-job reporting. |
| `TestMicroserviceQAIntegrationE2E` | Schema-change routing and multi-PR metrics smoke test. |

---

## 7. Cleanup Performed

Temporary Phase 4 SDK containers were removed:

```text
carbon-aware-sdk-phase4-file
carbon-aware-sdk-phase4-emissions
carbon-aware-sdk-phase4-mounted
carbon-aware-sdk-phase4-json
carbon-aware-sdk-phase4-debug
```

Temporary JSON datasource folder was removed:

```text
.tmp/phase4-carbon-json
```

---

## 8. Empirical Evaluation Interpretation

For the research paper, this Phase 4 run supports the claim that the prototype can execute end-to-end with:

1. A live Docker-hosted Carbon Aware SDK WebAPI.
2. Real agentic QA pipeline integration in memory mode.
3. Risk-based routing across LOW, HIGH, and CRITICAL examples.
4. No observed quality-regression failures in the clean-diff checks.
5. Metrics and carbon-savings smoke checks passing.

Important limitation:

This run did not validate WattTime or Electricity Maps live provider data. It validated Docker-hosted SDK integration using a local JSON emissions datasource and the scheduler fallback path when the SDK returned no response body.

