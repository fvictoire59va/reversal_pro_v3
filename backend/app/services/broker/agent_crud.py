"""
Agent CRUD Mixin — create / update / delete / list agents, logging,
and statistics queries.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent, AgentPosition, AgentLog
from ...cache import get_redis_client
from ..telegram_service import telegram_service

logger = logging.getLogger(__name__)


class AgentCrudMixin:
    """CRUD operations, logging helpers, and aggregate statistics."""

    def __init__(self):
        self._running_agents: dict[int, bool] = {}
        self._redis = get_redis_client()

    # ── Agent CRUD ───────────────────────────────────────────

    async def create_agent(
        self,
        db: AsyncSession,
        symbol: str,
        timeframe: str,
        trade_amount: float = 100.0,
        mode: str = "paper",
        sensitivity: str = "Medium",
        signal_mode: str = "Confirmed Only",
        analysis_limit: int = 500,
    ) -> Agent:
        """Create a new agent with auto-generated name."""
        agent = Agent(
            name=f"agent_temp_{datetime.now(timezone.utc).timestamp()}",
            symbol=symbol,
            timeframe=timeframe,
            trade_amount=trade_amount,
            balance=trade_amount,
            is_active=False,
            mode=mode,
            sensitivity=sensitivity,
            signal_mode=signal_mode,
            analysis_limit=analysis_limit,
        )
        db.add(agent)
        await db.flush()

        agent.name = f"agent_{agent.id}"
        await db.commit()
        await db.refresh(agent)

        await self._log(db, agent.id, "AGENT_CREATED", {
            "symbol": symbol, "timeframe": timeframe,
            "trade_amount": trade_amount, "mode": mode,
            "sensitivity": sensitivity, "signal_mode": signal_mode,
            "analysis_limit": analysis_limit,
        })

        logger.info(f"Agent created: {agent.name} ({symbol} {timeframe} {mode} {sensitivity})")
        return agent

    async def delete_agent(self, db: AsyncSession, agent_id: int) -> bool:
        """Delete agent — all positions and logs will be cascade deleted."""
        agent = await db.get(Agent, agent_id)
        if not agent:
            return False

        agent_name = agent.name
        self._running_agents.pop(agent_id, None)

        await db.delete(agent)
        await db.commit()

        logger.info(f"Agent deleted: {agent_name} (all positions and logs cascade deleted)")
        return True

    async def toggle_agent(self, db: AsyncSession, agent_id: int) -> Optional[Agent]:
        """Toggle agent active/inactive."""
        agent = await db.get(Agent, agent_id)
        if not agent:
            return None

        agent.is_active = not agent.is_active
        agent.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(agent)

        status = "ACTIVATED" if agent.is_active else "DEACTIVATED"
        await self._log(db, agent.id, f"AGENT_{status}", {})

        if not agent.is_active:
            self._running_agents.pop(agent_id, None)

        if agent.is_active:
            await telegram_service.notify_agent_activated(
                agent.name, agent.symbol, agent.timeframe, agent.mode
            )
        else:
            await telegram_service.notify_agent_deactivated(agent.name)

        logger.info(f"Agent {agent.name}: {status}")
        return agent

    async def get_all_agents(self, db: AsyncSession) -> List[Agent]:
        """Get all agents."""
        result = await db.execute(
            select(Agent).order_by(Agent.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_agent(self, db: AsyncSession, agent_id: int) -> Optional[Agent]:
        return await db.get(Agent, agent_id)

    # ── Position queries ─────────────────────────────────────

    async def get_all_open_positions(self, db: AsyncSession) -> List[AgentPosition]:
        """Get all open positions across all agents."""
        result = await db.execute(
            select(AgentPosition)
            .where(AgentPosition.status == "OPEN")
            .order_by(AgentPosition.opened_at.desc())
        )
        return list(result.scalars().all())

    async def get_agent_positions(
        self, db: AsyncSession, agent_id: int, status: Optional[str] = None
    ) -> List[AgentPosition]:
        """Get positions for a specific agent."""
        query = select(AgentPosition).where(AgentPosition.agent_id == agent_id)
        if status:
            query = query.where(AgentPosition.status == status)
        query = query.order_by(AgentPosition.opened_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _get_open_positions(self, db: AsyncSession, agent_id: int) -> List[AgentPosition]:
        result = await db.execute(
            select(AgentPosition)
            .where(AgentPosition.agent_id == agent_id, AgentPosition.status == "OPEN")
        )
        return list(result.scalars().all())

    # ── Agent logs ───────────────────────────────────────────

    async def get_agent_logs(
        self, db: AsyncSession, agent_id: int, limit: int = 50
    ) -> List[AgentLog]:
        result = await db.execute(
            select(AgentLog)
            .where(AgentLog.agent_id == agent_id)
            .order_by(AgentLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _log(self, db: AsyncSession, agent_id: int, action: str, details: dict):
        """Write an agent activity log entry."""
        log = AgentLog(agent_id=agent_id, action=action, details=details)
        db.add(log)
        await db.commit()

    # ── Statistics ───────────────────────────────────────────

    async def get_agent_stats(self, db: AsyncSession, agent_id: int) -> dict:
        """Get statistics for an agent."""
        agent = await db.get(Agent, agent_id)
        if not agent:
            return {"open_positions": 0, "total_pnl": 0, "total_unrealized_pnl": 0}

        open_result = await db.execute(text(
            "SELECT COUNT(*) FROM agent_positions "
            "WHERE agent_id = :id AND status = 'OPEN'"
        ), {"id": agent_id})
        open_count = open_result.scalar()

        realized_result = await db.execute(text(
            "SELECT COALESCE(SUM(pnl), 0) FROM agent_positions "
            "WHERE agent_id = :id AND status IN ('CLOSED', 'STOPPED')"
        ), {"id": agent_id})
        total_pnl = realized_result.scalar()

        unrealized_result = await db.execute(text(
            "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM agent_positions "
            "WHERE agent_id = :id AND status = 'OPEN'"
        ), {"id": agent_id})
        total_unrealized_pnl = unrealized_result.scalar()

        return {
            "open_positions": open_count,
            "total_pnl": round(float(total_pnl), 4),
            "total_unrealized_pnl": round(float(total_unrealized_pnl), 4),
        }

    async def get_total_realized_pnl(self, db: AsyncSession) -> float:
        """Get total realized PnL across all agents."""
        result = await db.execute(text(
            "SELECT COALESCE(SUM(pnl), 0) FROM agent_positions "
            "WHERE status IN ('CLOSED', 'STOPPED')"
        ))
        return round(float(result.scalar()), 4)

    async def get_all_agent_stats(self, db: AsyncSession) -> dict[int, dict]:
        """Get stats for ALL agents in a single query (avoids N+1)."""
        result = await db.execute(text("""
            SELECT agent_id,
                   COUNT(*)    FILTER (WHERE status = 'OPEN')                         AS open_positions,
                   COALESCE(SUM(pnl), 0) FILTER (WHERE status IN ('CLOSED','STOPPED')) AS total_pnl,
                   COALESCE(SUM(unrealized_pnl), 0) FILTER (WHERE status = 'OPEN')     AS total_unrealized_pnl
            FROM agent_positions
            GROUP BY agent_id
        """))
        stats_map: dict[int, dict] = {}
        for row in result.fetchall():
            stats_map[row[0]] = {
                "open_positions": row[1],
                "total_pnl": round(float(row[2]), 4),
                "total_unrealized_pnl": round(float(row[3]), 4),
            }
        return stats_map
