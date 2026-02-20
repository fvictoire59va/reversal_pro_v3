"""
Agent Broker Service — autonomous trading agents that monitor signals
and manage positions on Hyperliquid.

Each agent:
  1. Polls signals from DB at its configured timeframe interval
  2. Opens LONG on bullish reversal, closes on bearish reversal
  3. Opens SHORT on bearish reversal, closes on bullish reversal
  4. Calculates SL from previous pivot, TP with TF-adaptive R:R (1.5-3:1)
  5. Works in paper (simulation) or live mode

The service is composed of specialised mixins (see ``broker/`` sub-package):
  - AgentCrudMixin        - create / update / delete / list agents & logging
  - SignalEvaluatorMixin  - signal retrieval, staleness, trend filters
  - RiskManagerMixin      - SL/TP calculation, trailing stop, breakeven
  - PositionManagerMixin  - open / close / partial-TP / unrealised PnL
  - AgentOrchestratorMixin - execution cycle and scheduling
"""

from .broker import (
    TIMEFRAME_SECONDS,
    HTF_MAP,
    AgentCrudMixin,
    SignalEvaluatorMixin,
    RiskManagerMixin,
    PositionManagerMixin,
    AgentOrchestratorMixin,
)


class AgentBrokerService(
    AgentCrudMixin,
    SignalEvaluatorMixin,
    RiskManagerMixin,
    PositionManagerMixin,
    AgentOrchestratorMixin,
):
    """Manages all trading agents and their autonomous execution.

    Composed of specialised mixins — see each module under ``broker/``
    for implementation details.
    """

    pass


# Re-export constants for backward compatibility
__all__ = ["AgentBrokerService", "TIMEFRAME_SECONDS", "HTF_MAP"]


# Backward-compatible singleton - delegates to centralized dependencies
def __getattr__(name):
    if name == "agent_broker_service":
        from ..dependencies import get_agent_broker_service
        return get_agent_broker_service()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
