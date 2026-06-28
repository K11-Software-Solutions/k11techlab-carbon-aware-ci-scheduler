# Phase 5 Carbon Accounting Summary

**Run date:** 2026-06-25  
**Outcome:** PASS  
**Command:** `python -m pytest tests/test_metrics.py -v`  
**Result:** `20 passed in 7.15s`

---

## What Phase 5 Validated

| Area | Evidence |
|------|----------|
| CO2 savings formula | Savings increase with intensity gap, stay non-negative, and match `carbon_cost_grams()` deltas. |
| Scheduler metrics | Submitted, deferred, immediate, pending, and Prometheus-compatible metric keys passed. |
| DeferEngine accumulation | Deferred jobs add CO2 savings; cancelled and immediate jobs do not incorrectly contribute. |
| Carbon threshold behavior | Exact threshold and below-threshold behavior passed. |
| SCI components | Energy scales linearly with intensity, PUE, and TDP; units remain grams, not kg. |

## Interpretation

Phase 5 supports the paper's carbon-accounting validity claim: the prototype's reported savings and observability metrics are internally consistent with the cost model and expected Software Carbon Intensity scaling behavior.

