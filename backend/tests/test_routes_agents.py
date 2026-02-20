"""Integration tests for /api/v1/agents routes.

Uses the HTTPX async client with an in-memory SQLite DB.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text


# ============================================================================
# Agent CRUD
# ============================================================================

@pytest.mark.asyncio
class TestAgentCRUD:

    async def test_create_agent(self, client: AsyncClient):
        """POST /api/v1/agents → 200 + agent JSON."""
        resp = await client.post("/api/v1/agents", json={
            "name": "test_btc",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "trade_amount": 50.0,
            "mode": "paper",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test_btc"
        assert data["symbol"] == "BTC/USDT"
        assert data["is_active"] is False

    async def test_create_duplicate_name_fails(self, client: AsyncClient):
        """Creating two agents with the same name should fail."""
        payload = {
            "name": "dup_agent",
            "symbol": "ETH/USDT",
            "timeframe": "1h",
            "trade_amount": 100.0,
        }
        resp1 = await client.post("/api/v1/agents", json=payload)
        assert resp1.status_code == 200

        resp2 = await client.post("/api/v1/agents", json=payload)
        assert resp2.status_code in (400, 409, 500)  # depends on how error is surfaced

    async def test_list_agents_empty(self, client: AsyncClient):
        """GET /api/v1/agents → 200, returns list."""
        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_list_agents_after_create(self, client: AsyncClient):
        """Create an agent then list → should contain the agent."""
        await client.post("/api/v1/agents", json={
            "name": "list_test",
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "trade_amount": 100.0,
        })
        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()]
        assert "list_test" in names

    async def test_get_agent_by_id(self, client: AsyncClient):
        """GET /api/v1/agents/{id} → 200 + correct agent."""
        create = await client.post("/api/v1/agents", json={
            "name": "get_me",
            "symbol": "SOL/USDT",
            "timeframe": "1h",
            "trade_amount": 25.0,
        })
        agent_id = create.json()["id"]

        resp = await client.get(f"/api/v1/agents/{agent_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "get_me"

    async def test_get_nonexistent_agent_404(self, client: AsyncClient):
        """GET /api/v1/agents/99999 → 404."""
        resp = await client.get("/api/v1/agents/99999")
        assert resp.status_code == 404

    async def test_delete_agent(self, client: AsyncClient):
        """DELETE /api/v1/agents/{id} → 200, then GET → 404."""
        create = await client.post("/api/v1/agents", json={
            "name": "delete_me",
            "symbol": "ETH/USDT",
            "timeframe": "1h",
            "trade_amount": 50.0,
        })
        agent_id = create.json()["id"]

        del_resp = await client.delete(f"/api/v1/agents/{agent_id}")
        assert del_resp.status_code == 200

        get_resp = await client.get(f"/api/v1/agents/{agent_id}")
        assert get_resp.status_code == 404


# ============================================================================
# Positions for chart
# ============================================================================

@pytest.mark.asyncio
class TestPositionsForChart:

    async def test_no_positions_returns_empty(self, client: AsyncClient):
        """GET /api/v1/agents/positions-by-chart/BTC-USDT/1h → empty list."""
        resp = await client.get("/api/v1/agents/positions-by-chart/BTC-USDT/1h")
        assert resp.status_code == 200
        assert resp.json()["positions"] == []


# ============================================================================
# Health check (deep)
# ============================================================================

@pytest.mark.asyncio
class TestHealthCheck:

    async def test_health_endpoint(self, client: AsyncClient):
        """GET /health → 200 with status key."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
