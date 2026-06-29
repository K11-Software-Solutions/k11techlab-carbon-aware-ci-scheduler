# Copyright 2026 Kavita Jadhav, K11 Software Solutions LLC.
# SPDX-License-Identifier: Apache-2.0
"""
settings.py
===========
Centralised configuration for the carbon-aware CI scheduler.
All values are read from environment variables with documented defaults.
Copy .env.example (repo root) to .env and fill in your values.
"""

from __future__ import annotations
import os


# ── Carbon Aware SDK ─────────────────────────────────────────────────────────
CARBON_SDK_BASE_URL    = os.getenv("CARBON_SDK_BASE_URL",    "http://localhost:8090")
CARBON_SDK_ZONE        = os.getenv("CARBON_SDK_ZONE",        "eastus")
CARBON_HIGH_THRESHOLD  = float(os.getenv("CARBON_HIGH_THRESHOLD",  "400"))  # gCO2eq/kWh
CARBON_SEARCH_HOURS    = int(os.getenv("CARBON_SEARCH_HOURS",    "6"))

# ── Scheduler ────────────────────────────────────────────────────────────────
DEFERRED_TIMEOUT_HOURS = int(os.getenv("DEFERRED_TIMEOUT_HOURS", "8"))
LOG_LEVEL              = os.getenv("LOG_LEVEL", "INFO").upper()

# ── QA Pipeline Integration ──────────────────────────────────────────────────
QA_PIPELINE_MODULE     = os.getenv("QA_PIPELINE_MODULE",  "k11techlab.runner")
QA_PIPELINE_TIMEOUT    = int(os.getenv("QA_PIPELINE_TIMEOUT", "600"))

# ── Risk Routing ─────────────────────────────────────────────────────────────
# Override tier thresholds via env if needed
RISK_LOW_MAX           = float(os.getenv("RISK_LOW_MAX",    "0.40"))
RISK_MEDIUM_MAX        = float(os.getenv("RISK_MEDIUM_MAX", "0.70"))
RISK_HIGH_MAX          = float(os.getenv("RISK_HIGH_MAX",   "0.90"))

# ── Cost Model ───────────────────────────────────────────────────────────────
DEPTH_DECAY            = float(os.getenv("DEPTH_DECAY",    "0.70"))   # transitive graph decay
DRIFT_SENSITIVITY      = float(os.getenv("DRIFT_SENSITIVITY", "0.45"))
