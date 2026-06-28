# Phase 4 Live Docker Testing Guide

**Project:** K11tech Carbon-Aware CI Scheduler  
**Purpose:** Run Phase 4 end-to-end tests with Docker, the Carbon Aware SDK WebAPI, and the real QA pipeline repositories.  
**Last validated:** 2026-06-25  
**Related docs:** [test-strategy.md](./test-strategy.md), [test-evaluation-results.md](./test-evaluation-results.md), [phase-4-step-by-step-test-run-log.md](./phase-4-step-by-step-test-run-log.md), [carbon-aware-sdk-guide.md](./carbon-aware-sdk-guide.md), [empirical results](../results)

---

## What Phase 4 Validates

Phase 4 exercises the full deployed-style path:

1. `CarbonAwareScheduler` fetches carbon intensity from a live Carbon Aware SDK WebAPI container.
2. Routing decisions are made for LOW, HIGH, and CRITICAL risk PRs.
3. The real `k11techlab-agentic-ai-qa-system` pipeline is invoked in memory mode.
4. `k11techlab-microservice-qa-system` is present as the QA target repository.
5. Quality-regression, carbon-savings, metrics, and microservice smoke checks run together.

The test file is:

```bash
tests/e2e/test_carbon_scheduling_e2e.py
```

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Docker Desktop | Must be running before the SDK container can start. |
| Python 3.11+ | Validated with Python 3.11.0. |
| Scheduler dependencies | `pytest`, `pytest-asyncio`, `respx`, `httpx`, APScheduler dependencies. |
| Agentic QA repo | Expected at `../k11techlab-agentic-ai-qa-system`. |
| Microservice QA repo | Expected at `../k11techlab-microservice-qa-system`. |
| Carbon SDK image | `ghcr.io/green-software-foundation/carbon-aware-sdk:latest`. |

Verify Docker is ready:

```powershell
docker version
docker ps
```

If Docker Desktop is installed but not running:

```powershell
Start-Process -FilePath 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -WindowStyle Hidden
```

Then wait until `docker version` shows both Client and Server sections.

---

## Important SDK Notes

The simple command shown in the original strategy:

```bash
docker run -p 8090:8090 ghcr.io/green-software-foundation/carbon-aware-sdk:latest
```

did not work during live validation.

Observed issues:

| Issue | Cause / Finding |
|-------|-----------------|
| `No data sources are configured` | The SDK image requires datasource configuration. |
| `carbon-aware-sdk-api:latest` denied | The guide's `carbon-aware-sdk-api` image was not pullable from the registry in this environment. |
| Port mismatch | The available `carbon-aware-sdk:latest` image listens on container port `8080`, not `8090`. |
| JSON forecast unsupported | The SDK JSON datasource supports emissions, but not forecast data. |
| Readiness returned `204` | The readiness endpoint accepted the request but returned no body; the scheduler used its conservative fallback path. |

Because of this, the working Phase 4 setup used the SDK container with a mounted local JSON emissions datasource.

---

## Create Local JSON Emissions Data

From the scheduler repo root:

```powershell
$dataDir = Join-Path (Get-Location) '.tmp\phase4-carbon-json'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$start = [DateTimeOffset]::UtcNow.AddHours(-1)
$points = @()

for ($i = 0; $i -lt 20; $i++) {
    $t = $start.AddMinutes(30 * $i)
    $rating = if ($i % 5 -eq 0) { 180 } elseif ($i % 3 -eq 0) { 520 } else { 320 }

    $points += [ordered]@{
        location = 'eastus'
        time = $t.ToString('yyyy-MM-ddTHH:mm:ssZ')
        rating = $rating
        duration = '00:30:00'
    }
}

[ordered]@{ emissions = $points } |
    ConvertTo-Json -Depth 5 |
    Set-Content -Path (Join-Path $dataDir 'eastus.json') -Encoding UTF8
```

This creates:

```text
.tmp/phase4-carbon-json/eastus.json
```

---

## Start the Carbon SDK Container

Stop any old Phase 4 SDK containers first:

```powershell
docker ps -a --filter name=carbon-aware-sdk-phase4 --format "{{.Names}}" |
    ForEach-Object {
        if ($_ ) {
            docker stop $_ 2>$null
            docker rm $_ 2>$null
        }
    }
```

Start the SDK with the local JSON emissions file:

```powershell
$dataDir = Join-Path (Get-Location) '.tmp\phase4-carbon-json'

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

Verify container status:

```powershell
docker ps --filter name=carbon-aware-sdk-phase4-file
docker logs --tail 80 carbon-aware-sdk-phase4-file
```

Probe the endpoint used by the E2E fixture:

```powershell
Invoke-WebRequest `
  -Uri http://localhost:8090/emissions/bylocations/best?location=eastus `
  -UseBasicParsing `
  -TimeoutSec 10 |
  Select-Object StatusCode,Content
```

During validation this returned:

```text
StatusCode: 204
Content: {}
```

That is acceptable for the current tests because `CarbonAwareClient` falls back conservatively when the SDK returns no usable body, and the E2E tests allow empty forecast windows.

---

## Run Phase 4

Set environment variables and run the E2E marker set:

```powershell
$env:PYTHONPATH = "..\k11techlab-agentic-ai-qa-system;..\k11techlab-microservice-qa-system;$env:PYTHONPATH"
$env:QA_PIPELINE_MODULE = "pipeline.runner"
$env:CARBON_SDK_BASE_URL = "http://localhost:8090"
$env:CARBON_SDK_ZONE = "eastus"
$env:CARBON_HIGH_THRESHOLD = "400"

python -m pytest tests\e2e\ -o addopts="" -m "e2e and slow" -v --tb=long
```

Why this differs from the strategy command:

- `pytest.ini` excludes `e2e` and `slow` tests by default.
- The documented `--e2e` and `--slow` CLI flags are not registered pytest options in this repo.
- Clearing `addopts` and selecting markers directly is the working invocation.

Validated result on 2026-06-25:

```text
12 passed in 4.94s
```

---

## Expected Test Coverage

The Phase 4 run covers:

| Test group | What it checks |
|------------|----------------|
| `TestCarbonAwareRoutingE2E` | SDK intensity fetch, forecast handling, LOW/HIGH/CRITICAL routing. |
| `TestNoQualityRegressionE2E` | Cheap tier vs deferred/full tier verdict compatibility. |
| `TestCarbonSavingsMeasurementE2E` | Savings non-negativity, metrics counters, pending-job reporting. |
| `TestMicroserviceQAIntegrationE2E` | Schema-change routing and 5-PR metrics smoke test. |

---

## Cleanup

Stop and remove Phase 4 SDK containers:

```powershell
$containers = @(
  'carbon-aware-sdk-phase4-file',
  'carbon-aware-sdk-phase4-emissions',
  'carbon-aware-sdk-phase4-mounted',
  'carbon-aware-sdk-phase4-json',
  'carbon-aware-sdk-phase4-debug'
)

foreach ($name in $containers) {
    $exists = docker ps -a --filter "name=^/$name$" --format '{{.Names}}'
    if ($exists) {
        $running = docker ps --filter "name=^/$name$" --format '{{.Names}}'
        if ($running) { docker stop $name | Out-Null }
        docker rm $name | Out-Null
    }
}
```

Remove the temporary JSON datasource:

```powershell
Remove-Item -LiteralPath .tmp\phase4-carbon-json -Recurse -Force
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Docker says daemon pipe is missing | Docker Desktop is not running. | Launch Docker Desktop and wait for `docker version` to show Server info. |
| `No data sources are configured` | SDK started without datasource env vars. | Use the JSON emissions datasource command above. |
| Registry denies `carbon-aware-sdk-api` | Image unavailable or restricted. | Use `ghcr.io/green-software-foundation/carbon-aware-sdk:latest`. |
| Endpoint connection refused | Container is not running or wrong port mapping. | Use `-p 8090:8080` and check `docker ps`. |
| `JSON data source is not supported for forecast data` | JSON configured as `ForecastDataSource`. | Configure only `DataSources__EmissionsDataSource=Json`. |
| `Permission denied` from JSON datasource | `DataFileLocation` points to a directory. | Point it to `/app/data-sources/json/eastus.json`. |
| Pytest deselects E2E tests | Default `addopts` excludes `e2e` and `slow`. | Run with `-o addopts="" -m "e2e and slow"`. |

---

## Interpretation Caveat

This setup validates a live Docker-hosted Carbon Aware SDK WebAPI and the scheduler's resilience path, but it does not validate a real external grid provider such as WattTime or Electricity Maps. For release validation against real grid data, configure an SDK datasource with provider credentials and rerun the same Phase 4 pytest command.
