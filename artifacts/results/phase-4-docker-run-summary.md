# Phase 4 Docker Run Summary

**Run date:** 2026-06-25  
**Outcome:** PASS  
**Command:** `python -m pytest tests/e2e/ -o addopts="" -m "e2e and slow" -v --tb=long`  
**Result:** `12 passed in 4.94s`

---

## Docker Setup

Docker Desktop was initially not running. It was launched and confirmed ready with:

```powershell
docker version
```

The Carbon SDK image used was:

```text
ghcr.io/green-software-foundation/carbon-aware-sdk:latest
```

The image listens on container port `8080`, so the working local mapping was:

```text
localhost:8090 -> container:8080
```

---

## SDK Configuration Findings

| Attempt | Result |
|---------|--------|
| Bare SDK command | Container exited with `No data sources are configured`. |
| `carbon-aware-sdk-api:latest` | Registry returned `denied`. |
| JSON emissions plus forecast datasource | Container exited because JSON forecast is unsupported. |
| JSON emissions directory path | Endpoint returned 500 `Permission denied`. |
| JSON emissions file path | Container stayed up and endpoint returned HTTP 204. |

Final configuration used:

```powershell
-e DataSources__EmissionsDataSource=Json
-e DataSources__Configurations__Json__Type=Json
-e DataSources__Configurations__Json__DataFileLocation=/app/data-sources/json/eastus.json
-e DataSources__Configurations__Json__CacheJsonData=true
```

---

## Test Interpretation

The Phase 4 test run validates:

- Docker-hosted Carbon Aware SDK WebAPI integration.
- Scheduler behavior when the SDK is reachable but returns no usable body.
- Real agentic QA pipeline adapter execution in memory mode.
- Microservice QA repository availability for system-level smoke tests.

It does not validate live external provider data from WattTime or Electricity Maps.

