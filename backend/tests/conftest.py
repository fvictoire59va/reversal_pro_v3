"""Shared pytest fixtures for Reversal Detection Pro backend tests.

Provides:
- An async SQLite-backed database for fast isolated tests
- An HTTPX async client wired to the FastAPI app
- A pre-built AgentBrokerService for unit tests
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ── Force test-friendly config before any app code is imported ──
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("AUTO_REFRESH_ENABLED", "false")

from app.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.database import get_db  # noqa: E402


# ---------------------------------------------------------------------------
# Event-loop fixture (session-scoped so all async tests share one loop)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# In-memory async SQLite engine & session factory
# ---------------------------------------------------------------------------
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    echo=False,
)
_TestSessionFactory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    """Create all tables before each test, drop after."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a fresh async DB session for unit tests."""
    async with _TestSessionFactory() as session:
        yield session


# ---------------------------------------------------------------------------
# Override FastAPI's `get_db` dependency → test DB
# ---------------------------------------------------------------------------
async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _TestSessionFactory() as session:
        try:
            yield session
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


# ---------------------------------------------------------------------------
# HTTPX async client for integration tests
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# AgentBrokerService instance for unit tests
# ---------------------------------------------------------------------------
@pytest.fixture
def broker_service():
    """Return an AgentBrokerService instance (stateless helper methods)."""
    from app.services.agent_broker import AgentBrokerService
    return AgentBrokerService()
