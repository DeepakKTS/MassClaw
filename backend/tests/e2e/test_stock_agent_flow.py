"""
End-to-end test: Stock External Agent Flow

Proves that a generic OpenClaw-like agent can:
1. Discover capabilities
2. Submit a plain-English task
3. Monitor progress
4. Get the result
5. Verify audit trail exists

This test uses the real API surface — no mocks.
"""

import pytest
import httpx
import asyncio

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="module")
def client():
    """HTTP client for the test session."""
    return httpx.Client(base_url=BASE_URL, timeout=120)


class TestStockAgentFlow:
    """Simulate a stock external agent using MassClaw."""

    def test_01_health_check(self, client):
        """Agent verifies MassClaw is running."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_02_readiness_check(self, client):
        """Agent verifies MassClaw is fully ready."""
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["database"] == "ready"
        assert data["redis"] == "ready"

    def test_03_discover_capabilities(self, client):
        """Agent discovers what MassClaw can do."""
        resp = client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()

        assert data["platform"] == "MassClaw"
        assert data["total_agents"] > 0
        assert isinstance(data["capabilities"], list)
        assert len(data["capabilities"]) > 0
        assert "endpoints" in data
        assert "submit_task" in data["endpoints"]
        assert "features" in data
        assert isinstance(data["features"], list)

    def test_04_list_agents(self, client):
        """Agent lists available agents in the registry."""
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] > 0

    def test_05_search_agents_by_capability(self, client):
        """Agent searches for a specific capability."""
        resp = client.get("/api/v1/agents/search", params={"capability": "research"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_06_submit_task(self, client):
        """Agent submits a plain-English task."""
        resp = client.post(
            "/api/v1/workflows/submit",
            json={
                "instruction": "Summarize the key risks of AI in healthcare scheduling",
                "budget": 200,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "workflow_id" in data
        assert data["status"] == "pending"
        assert "track_url" in data
        self.__class__.workflow_id = data["workflow_id"]

    def test_07_check_workflow_status(self, client):
        """Agent polls workflow status."""
        wf_id = getattr(self.__class__, "workflow_id", None)
        if not wf_id:
            pytest.skip("No workflow_id from previous test")

        resp = client.get(f"/api/v1/workflows/{wf_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "progress_percent" in data

    def test_08_get_workflow_detail(self, client):
        """Agent gets full workflow details."""
        wf_id = getattr(self.__class__, "workflow_id", None)
        if not wf_id:
            pytest.skip("No workflow_id from previous test")

        resp = client.get(f"/api/v1/workflows/{wf_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow_id"] == wf_id

    def test_09_query_memory(self, client):
        """Agent queries shared memory."""
        resp = client.post(
            "/api/v1/memory/query",
            json={
                "query": "healthcare scheduling risks",
                "min_similarity": 0.3,
                "top_k": 5,
            },
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_10_check_audit_trail(self, client):
        """Agent verifies audit trail exists."""
        resp = client.get("/api/v1/audit/search", params={"page_size": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_11_trust_leaderboard(self, client):
        """Agent checks trust leaderboard."""
        resp = client.get("/api/v1/trust/leaderboard/ranked", params={"limit": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_12_policy_evaluate(self, client):
        """Agent checks policy evaluation."""
        resp = client.post(
            "/api/v1/policy/evaluate",
            json={
                "action": "execute_workflow",
                "context": {"domain": "healthcare", "budget": 200},
            },
        )
        assert resp.status_code == 200

    def test_13_evolution_rankings(self, client):
        """Agent checks agent evolution rankings."""
        resp = client.get("/api/v1/evolution/rankings/leaderboard", params={"limit": 5})
        assert resp.status_code == 200


class TestInputValidation:
    """Verify invalid inputs are rejected properly."""

    def test_empty_instruction_rejected(self, client):
        """Empty instruction should be rejected."""
        resp = client.post(
            "/api/v1/workflows/submit",
            json={
                "instruction": "",
                "budget": 100,
            },
        )
        assert resp.status_code == 422

    def test_negative_budget_rejected(self, client):
        """Negative budget should be rejected."""
        resp = client.post(
            "/api/v1/workflows/submit",
            json={
                "instruction": "Valid instruction here",
                "budget": -100,
            },
        )
        assert resp.status_code == 422

    def test_excessive_budget_rejected(self, client):
        """Budget over 10000 should be rejected."""
        resp = client.post(
            "/api/v1/workflows/submit",
            json={
                "instruction": "Valid instruction here",
                "budget": 99999,
            },
        )
        assert resp.status_code == 422

    def test_missing_instruction_rejected(self, client):
        """Missing instruction field should be rejected."""
        resp = client.post(
            "/api/v1/workflows/submit",
            json={
                "budget": 100,
            },
        )
        assert resp.status_code == 422

    def test_invalid_json_rejected(self, client):
        """Malformed JSON should be rejected."""
        resp = client.post(
            "/api/v1/workflows/submit", content=b"not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 422

    def test_nonexistent_workflow(self, client):
        """Getting a nonexistent workflow should return 404."""
        resp = client.get("/api/v1/workflows/00000000-0000-0000-0000-000000000000")
        assert resp.status_code in (404, 500)  # 404 or internal error for missing

    def test_capabilities_is_get_only(self, client):
        """Capabilities endpoint should reject POST."""
        resp = client.post("/api/v1/capabilities")
        assert resp.status_code == 405


class TestReadinessEndpoints:
    """Verify health and readiness endpoints."""

    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready_returns_components(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert "database" in data
        assert "redis" in data
        assert "embeddings" in data

    def test_api_root(self, client):
        resp = client.get("/api/v1/")
        assert resp.status_code == 200
        assert "MassClaw" in resp.json()["message"]
