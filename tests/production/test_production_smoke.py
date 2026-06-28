"""
Production smoke tests for a deployed carbon-aware scheduler app.

These tests are intentionally safe by default:
  - They skip unless PROD_BASE_URL is set.
  - They only perform read-only GET/HEAD/OPTIONS checks unless
    PROD_ALLOW_MUTATION=1 is explicitly set.
"""

from __future__ import annotations

import os
from typing import Iterable

import httpx
import pytest


pytestmark = pytest.mark.production


def _base_url() -> str:
    base = os.getenv("PROD_BASE_URL", "").strip().rstrip("/")
    if not base:
        pytest.skip("Set PROD_BASE_URL to run production smoke tests.")
    return base


def _headers() -> dict[str, str]:
    headers = {"User-Agent": "k11-carbon-aware-prod-smoke/1.0"}
    token = os.getenv("PROD_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _url(base: str, path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _acceptable_reachable_statuses() -> set[int]:
    # 401/403 are acceptable for reachability because many production health
    # endpoints are protected. 5xx is not acceptable.
    return {200, 201, 202, 204, 301, 302, 307, 308, 401, 403}


def _client() -> httpx.Client:
    timeout = float(os.getenv("PROD_TIMEOUT_SECONDS", "10"))
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers=_headers(),
    )


def _first_reachable(client: httpx.Client, base: str, paths: Iterable[str]) -> httpx.Response:
    responses: list[tuple[str, int | str]] = []
    for path in paths:
        try:
            response = client.get(_url(base, path))
        except httpx.HTTPError as exc:
            responses.append((path, exc.__class__.__name__))
            continue
        responses.append((path, response.status_code))
        if response.status_code in _acceptable_reachable_statuses():
            return response
    pytest.fail(f"No configured production smoke path was reachable: {responses}")


def test_production_app_reachable_read_only():
    base = _base_url()
    paths = _csv_env("PROD_SMOKE_PATHS", "/health,/metrics,/")
    with _client() as client:
        response = _first_reachable(client, base, paths)
    assert response.status_code in _acceptable_reachable_statuses()


def test_production_metrics_endpoint_if_available():
    base = _base_url()
    metrics_path = os.getenv("PROD_METRICS_PATH", "/metrics")
    with _client() as client:
        response = client.get(_url(base, metrics_path))

    if response.status_code == 404:
        pytest.skip(f"Metrics endpoint not exposed at {metrics_path}.")
    if response.status_code in {401, 403}:
        pytest.skip(f"Metrics endpoint exists but is protected: HTTP {response.status_code}.")

    assert response.status_code == 200
    body = response.text
    assert (
        "carbon_scheduler_" in body
        or "python_info" in body
        or "process_" in body
        or response.headers.get("content-type", "").startswith("text/plain")
    )


def test_production_webhook_contract_read_only_probe():
    base = _base_url()
    webhook_path = os.getenv("PROD_WEBHOOK_PATH", "/webhook/pr")
    with _client() as client:
        response = client.options(_url(base, webhook_path))

    # Any of these proves the app is reachable and the route/proxy made a
    # deliberate decision. A 404 means the documented webhook is not deployed.
    assert response.status_code in {200, 204, 401, 403, 405}


def test_production_webhook_canary_submit_explicit_opt_in():
    if os.getenv("PROD_ALLOW_MUTATION", "").strip() != "1":
        pytest.skip("Set PROD_ALLOW_MUTATION=1 to run the production webhook canary POST.")

    base = _base_url()
    webhook_path = os.getenv("PROD_WEBHOOK_PATH", "/webhook/pr")
    payload = {
        "pr_id": os.getenv("PROD_TEST_PR_ID", "prod-smoke-canary"),
        "risk_score": float(os.getenv("PROD_TEST_RISK_SCORE", "0.10")),
        "force_full": False,
        "metadata": {
            "source": "production_smoke_test",
            "expected_side_effect": "canary_scheduler_submission",
        },
    }

    with _client() as client:
        response = client.post(_url(base, webhook_path), json=payload)

    assert response.status_code in {200, 201, 202, 204}
