from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api import app as api_app
from scheduler.risk_router import RiskBucket, RoutingDecision


class FakeScheduler:
    def __init__(self) -> None:
        self.submitted = []

    def metrics(self) -> dict:
        return {
            "carbon_scheduler_prs_submitted_total": 1,
            "carbon_scheduler_immediate_jobs_total": 1,
            "carbon_scheduler_deferred_jobs_total": 0,
            "carbon_scheduler_co2_saved_grams_total": 0.0,
            "carbon_scheduler_jobs_pending": 0,
        }

    async def submit(self, pr):
        self.submitted.append(pr)
        return RoutingDecision(
            pr_id=pr.pr_id,
            immediate_agents=["api_agent"],
            deferred_agents=[],
            deferred_window=None,
            risk_bucket=RiskBucket.LOW,
            carbon_intensity=250.0,
            deferral_reason="test decision",
            decided_at=datetime.now(timezone.utc),
        )


def _client_with_scheduler(fake: FakeScheduler) -> TestClient:
    api_app.app.dependency_overrides[api_app._scheduler] = lambda: fake
    return TestClient(api_app.app)


def teardown_function():
    api_app.app.dependency_overrides.clear()
    api_app.API_TOKEN = ""


def test_health_endpoint():
    client = TestClient(api_app.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "carbon-aware-ci-scheduler"


def test_metrics_endpoint_prometheus_text():
    client = _client_with_scheduler(FakeScheduler())
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "carbon_scheduler_prs_submitted_total 1" in response.text


def test_webhook_options_is_read_only_probe():
    client = TestClient(api_app.app)
    response = client.options("/webhook/pr")
    assert response.status_code == 204
    assert "POST" in response.headers["allow"]


def test_submit_pr_webhook_returns_routing_decision():
    fake = FakeScheduler()
    client = _client_with_scheduler(fake)
    response = client.post(
        "/webhook/pr",
        json={"pr_id": "PR-123", "risk_score": 0.2, "metadata": {"source": "test"}},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["decision"]["pr_id"] == "PR-123"
    assert body["decision"]["risk_bucket"] == "low"
    assert fake.submitted[0].metadata == {"source": "test"}


def test_metrics_requires_token_when_configured():
    api_app.API_TOKEN = "secret"
    client = _client_with_scheduler(FakeScheduler())
    response = client.get("/metrics")
    assert response.status_code == 401

    response = client.get("/metrics", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200
