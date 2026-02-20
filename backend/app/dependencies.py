"""
Centralized dependency accessors for FastAPI Depends() injection.

Service instances are lazily created on first access so that importing this
module does NOT trigger side effects (network connections, etc.).

Usage in route handlers:
    from ..dependencies import get_analysis_service, get_ingestion_service
    @router.post("/run")
    async def run(service = Depends(get_analysis_service), db = Depends(get_db)): ...
"""

from __future__ import annotations

_ingestion_service = None
_analysis_service = None
_telegram_service = None
_agent_broker_service = None
_hyperliquid_client = None


def get_ingestion_service():
    """Lazily return the DataIngestionService singleton."""
    global _ingestion_service
    if _ingestion_service is None:
        from .services.data_ingestion import DataIngestionService
        _ingestion_service = DataIngestionService()
    return _ingestion_service


def get_analysis_service():
    """Lazily return the AnalysisService singleton."""
    global _analysis_service
    if _analysis_service is None:
        from .services.analysis_service import AnalysisService
        _analysis_service = AnalysisService()
    return _analysis_service


def get_telegram_service():
    """Lazily return the TelegramService singleton."""
    global _telegram_service
    if _telegram_service is None:
        from .services.telegram_service import TelegramService
        _telegram_service = TelegramService()
    return _telegram_service


def get_agent_broker_service():
    """Lazily return the AgentBrokerService singleton."""
    global _agent_broker_service
    if _agent_broker_service is None:
        from .services.agent_broker import AgentBrokerService
        _agent_broker_service = AgentBrokerService()
    return _agent_broker_service


def get_hyperliquid_client():
    """Lazily return the HyperliquidClient singleton."""
    global _hyperliquid_client
    if _hyperliquid_client is None:
        from .services.hyperliquid_client import HyperliquidClient
        _hyperliquid_client = HyperliquidClient()
    return _hyperliquid_client
