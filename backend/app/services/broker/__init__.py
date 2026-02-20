"""
Broker sub-package — splits the monolithic AgentBrokerService into
focused mixins that are re-composed in ``agent_broker.py``.

Modules
-------
constants          Shared lookup tables (TIMEFRAME_SECONDS, HTF_MAP)
agent_crud         CRUD operations, logging, statistics
signal_evaluator   Signal retrieval, staleness checks, trend filters
risk_manager       SL/TP calculation, trailing stop, breakeven
position_manager   Open / close / partial-TP / unrealised PnL
agent_orchestrator Execution cycle and scheduling
"""

from .constants import TIMEFRAME_SECONDS, HTF_MAP
from .agent_crud import AgentCrudMixin
from .signal_evaluator import SignalEvaluatorMixin
from .risk_manager import RiskManagerMixin
from .position_manager import PositionManagerMixin
from .agent_orchestrator import AgentOrchestratorMixin

__all__ = [
    "TIMEFRAME_SECONDS",
    "HTF_MAP",
    "AgentCrudMixin",
    "SignalEvaluatorMixin",
    "RiskManagerMixin",
    "PositionManagerMixin",
    "AgentOrchestratorMixin",
]
