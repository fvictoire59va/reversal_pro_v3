"""
Agent Broker API routes — CRUD agents, positions, manual close.
"""

import logging
from typing import Optional
from datetime import datetime, timezone
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import (
    AgentCreate, AgentUpdate, AgentResponse, PositionResponse,
    AgentLogResponse, AgentsOverview,
)
from ..services.agent_broker import agent_broker_service
from ..models import AgentPosition, Agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ── Agents CRUD ──────────────────────────────────────────────

@router.get("/", response_model=AgentsOverview)
async def get_agents_overview(db: AsyncSession = Depends(get_db)):
    """Get all agents with open positions and statistics."""
    agents = await agent_broker_service.get_all_agents(db)
    open_positions = await agent_broker_service.get_all_open_positions(db)

    # Build agent responses with stats
    agent_responses = []
    for agent in agents:
        stats = await agent_broker_service.get_agent_stats(db, agent.id)
        agent_responses.append(AgentResponse(
            id=agent.id,
            name=agent.name,
            symbol=agent.symbol,
            timeframe=agent.timeframe,
            trade_amount=agent.trade_amount,
            balance=agent.balance,
            is_active=agent.is_active,
            mode=agent.mode,
            sensitivity=agent.sensitivity,
            signal_mode=agent.signal_mode,
            analysis_limit=agent.analysis_limit,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            open_positions=stats["open_positions"],
            total_pnl=stats["total_pnl"],
            total_unrealized_pnl=stats["total_unrealized_pnl"],
        ))

    # Build position responses with agent names
    agent_name_map = {a.id: a.name for a in agents}
    position_responses = [
        PositionResponse(
            id=p.id,
            agent_id=p.agent_id,
            agent_name=agent_name_map.get(p.agent_id, "unknown"),
            symbol=p.symbol,
            side=p.side,
            entry_price=p.entry_price,
            exit_price=p.exit_price,
            stop_loss=p.stop_loss,
            take_profit=p.take_profit,
            quantity=p.quantity,
            status=p.status,
            pnl=p.pnl,
            pnl_percent=p.pnl_percent,
            unrealized_pnl=p.unrealized_pnl,
            unrealized_pnl_percent=p.unrealized_pnl_percent,
            current_price=p.current_price,
            pnl_updated_at=p.pnl_updated_at,
            opened_at=p.opened_at,
            closed_at=p.closed_at,
        )
        for p in open_positions
    ]

    total_pnl = await agent_broker_service.get_total_realized_pnl(db)

    return AgentsOverview(
        agents=agent_responses,
        open_positions=position_responses,
        total_agents=len(agents),
        active_agents=sum(1 for a in agents if a.is_active),
        total_open_positions=len(open_positions),
        total_realized_pnl=total_pnl,
    )


@router.post("/", response_model=AgentResponse)
async def create_agent(req: AgentCreate, db: AsyncSession = Depends(get_db)):
    """Create a new trading agent."""
    try:
        agent = await agent_broker_service.create_agent(
            db, req.symbol, req.timeframe, req.trade_amount, req.mode,
            req.sensitivity, req.signal_mode, req.analysis_limit,
        )
        stats = await agent_broker_service.get_agent_stats(db, agent.id)
        return AgentResponse(
            id=agent.id,
            name=agent.name,
            symbol=agent.symbol,
            timeframe=agent.timeframe,
            trade_amount=agent.trade_amount,
            balance=agent.balance,
            is_active=agent.is_active,
            mode=agent.mode,
            sensitivity=agent.sensitivity,
            signal_mode=agent.signal_mode,
            analysis_limit=agent.analysis_limit,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            open_positions=stats["open_positions"],
            total_pnl=stats["total_pnl"],
            total_unrealized_pnl=stats["total_unrealized_pnl"],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Delete an agent and close all its positions."""
    success = await agent_broker_service.delete_agent(db, agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted", "agent_id": agent_id}


@router.patch("/{agent_id}/toggle", response_model=AgentResponse)
async def toggle_agent(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Activate or deactivate an agent."""
    agent = await agent_broker_service.toggle_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    stats = await agent_broker_service.get_agent_stats(db, agent.id)
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        symbol=agent.symbol,
        timeframe=agent.timeframe,
        trade_amount=agent.trade_amount,
        balance=agent.balance,
        is_active=agent.is_active,
        mode=agent.mode,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        open_positions=stats["open_positions"],
        total_pnl=stats["total_pnl"],
        total_unrealized_pnl=stats["total_unrealized_pnl"],
    )


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: int, req: AgentUpdate, db: AsyncSession = Depends(get_db)):
    """Update agent settings (trade_amount, mode)."""
    agent = await agent_broker_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if req.trade_amount is not None:
        agent.trade_amount = req.trade_amount
    if req.mode is not None:
        agent.mode = req.mode

    await db.commit()
    await db.refresh(agent)

    stats = await agent_broker_service.get_agent_stats(db, agent.id)
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        symbol=agent.symbol,
        timeframe=agent.timeframe,
        trade_amount=agent.trade_amount,
        balance=agent.balance,
        is_active=agent.is_active,
        mode=agent.mode,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        open_positions=stats["open_positions"],
        total_pnl=stats["total_pnl"],
        total_unrealized_pnl=stats["total_unrealized_pnl"],
    )


# ── Positions ────────────────────────────────────────────────

@router.get("/positions", response_model=list[PositionResponse])
async def get_all_positions(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get all positions (optionally filtered by status)."""
    if status:
        from sqlalchemy import select
        from ..models import AgentPosition, Agent
        query = (
            select(AgentPosition)
            .where(AgentPosition.status == status.upper())
            .order_by(AgentPosition.opened_at.desc())
        )
        result = await db.execute(query)
        positions = list(result.scalars().all())
    else:
        positions = await agent_broker_service.get_all_open_positions(db)

    # Get agent names
    agents = await agent_broker_service.get_all_agents(db)
    name_map = {a.id: a.name for a in agents}

    return [
        PositionResponse(
            id=p.id,
            agent_id=p.agent_id,
            agent_name=name_map.get(p.agent_id, "unknown"),
            symbol=p.symbol,
            side=p.side,
            entry_price=p.entry_price,
            exit_price=p.exit_price,
            stop_loss=p.stop_loss,
            take_profit=p.take_profit,
            quantity=p.quantity,
            status=p.status,
            pnl=p.pnl,
            pnl_percent=p.pnl_percent,
            opened_at=p.opened_at,
            closed_at=p.closed_at,
        )
        for p in positions
    ]


@router.get("/{agent_id}/positions", response_model=list[PositionResponse])
async def get_agent_positions(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Get all positions for a specific agent."""
    positions = await agent_broker_service.get_agent_positions(db, agent_id)
    agent = await agent_broker_service.get_agent(db, agent_id)
    agent_name = agent.name if agent else "unknown"

    return [
        PositionResponse(
            id=p.id,
            agent_id=p.agent_id,
            agent_name=agent_name,
            symbol=p.symbol,
            side=p.side,
            entry_price=p.entry_price,
            exit_price=p.exit_price,
            stop_loss=p.stop_loss,
            take_profit=p.take_profit,
            quantity=p.quantity,
            status=p.status,
            pnl=p.pnl,
            pnl_percent=p.pnl_percent,
            opened_at=p.opened_at,
            closed_at=p.closed_at,
        )
        for p in positions
    ]


@router.post("/positions/{position_id}/close", response_model=PositionResponse)
async def close_position(position_id: int, db: AsyncSession = Depends(get_db)):
    """Manually close a position."""
    pos = await agent_broker_service.close_position_manually(db, position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found or already closed")

    agent = await agent_broker_service.get_agent(db, pos.agent_id)
    return PositionResponse(
        id=pos.id,
        agent_id=pos.agent_id,
        agent_name=agent.name if agent else "unknown",
        symbol=pos.symbol,
        side=pos.side,
        entry_price=pos.entry_price,
        exit_price=pos.exit_price,
        stop_loss=pos.stop_loss,
        take_profit=pos.take_profit,
        quantity=pos.quantity,
        status=pos.status,
        pnl=pos.pnl,
        pnl_percent=pos.pnl_percent,
        opened_at=pos.opened_at,
        closed_at=pos.closed_at,
    )


# ── Logs ─────────────────────────────────────────────────────

@router.get("/{agent_id}/logs", response_model=list[AgentLogResponse])
async def get_agent_logs(agent_id: int, limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Get activity logs for an agent."""
    logs = await agent_broker_service.get_agent_logs(db, agent_id, limit)
    return [
        AgentLogResponse(
            id=log.id,
            agent_id=log.agent_id,
            action=log.action,
            details=log.details,
            created_at=log.created_at,
        )
        for log in logs
    ]


# ── Performance Tree ────────────────────────────────────────

@router.get("/{agent_id}/performance")
async def get_agent_performance(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Get hierarchical performance data for the agent tree view."""
    agent = await agent_broker_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get ALL positions for this agent (open + closed + stopped)
    result = await db.execute(
        select(AgentPosition)
        .where(AgentPosition.agent_id == agent_id)
        .order_by(AgentPosition.opened_at.desc())
    )
    positions = list(result.scalars().all())

    stats = await agent_broker_service.get_agent_stats(db, agent_id)

    # Helper to build stats from a list of positions
    def compute_stats(pos_list):
        if not pos_list:
            return {"count": 0, "pnl": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "avg_pnl": 0, "best": 0, "worst": 0, "avg_duration_min": 0}
        closed = [p for p in pos_list if p.status in ("CLOSED", "STOPPED")]
        total_pnl = sum(p.pnl or 0 for p in closed)
        wins = [p for p in closed if (p.pnl or 0) > 0]
        losses = [p for p in closed if (p.pnl or 0) <= 0]
        win_rate = (len(wins) / len(closed) * 100) if closed else 0
        avg_pnl = total_pnl / len(closed) if closed else 0
        best = max((p.pnl or 0) for p in closed) if closed else 0
        worst = min((p.pnl or 0) for p in closed) if closed else 0

        durations = []
        for p in closed:
            if p.opened_at and p.closed_at:
                dur = (p.closed_at - p.opened_at).total_seconds() / 60
                durations.append(dur)
        avg_dur = sum(durations) / len(durations) if durations else 0

        return {
            "count": len(pos_list),
            "closed_count": len(closed),
            "open_count": len([p for p in pos_list if p.status == "OPEN"]),
            "pnl": round(total_pnl, 4),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(avg_pnl, 4),
            "best": round(best, 4),
            "worst": round(worst, 4),
            "avg_duration_min": round(avg_dur, 1),
        }

    def pos_to_dict(p):
        return {
            "id": p.id,
            "side": p.side,
            "entry_price": p.entry_price,
            "exit_price": p.exit_price,
            "pnl": round(p.pnl, 4) if p.pnl else None,
            "pnl_percent": round(p.pnl_percent, 2) if p.pnl_percent else None,
            "unrealized_pnl": round(p.unrealized_pnl, 4) if p.unrealized_pnl else None,
            "status": p.status,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
            "quantity": p.quantity,
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
        }

    # ── Group by side ──
    long_positions = [p for p in positions if p.side == "LONG"]
    short_positions = [p for p in positions if p.side == "SHORT"]

    # ── Group by date (Paris timezone) ──
    by_date = defaultdict(list)
    for p in positions:
        if p.opened_at:
            # Convert to Paris date string
            paris_date = p.opened_at.strftime("%Y-%m-%d")
            by_date[paris_date].append(p)

    date_nodes = []
    for date_str in sorted(by_date.keys(), reverse=True):
        day_positions = by_date[date_str]
        date_nodes.append({
            "date": date_str,
            "stats": compute_stats(day_positions),
            "positions": [pos_to_dict(p) for p in day_positions],
        })

    # ── Group by status ──
    stopped = [p for p in positions if p.status == "STOPPED"]
    closed_ok = [p for p in positions if p.status == "CLOSED"]
    open_pos = [p for p in positions if p.status == "OPEN"]

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "symbol": agent.symbol,
            "timeframe": agent.timeframe,
            "trade_amount": agent.trade_amount,
            "balance": agent.balance,
            "is_active": agent.is_active,
            "mode": agent.mode,
        },
        "summary": {
            **compute_stats(positions),
            "total_pnl": stats["total_pnl"],
            "unrealized_pnl": stats["total_unrealized_pnl"],
        },
        "by_side": {
            "LONG": {
                "stats": compute_stats(long_positions),
                "positions": [pos_to_dict(p) for p in long_positions],
            },
            "SHORT": {
                "stats": compute_stats(short_positions),
                "positions": [pos_to_dict(p) for p in short_positions],
            },
        },
        "by_date": date_nodes,
        "by_status": {
            "OPEN": {
                "stats": compute_stats(open_pos),
                "positions": [pos_to_dict(p) for p in open_pos],
            },
            "CLOSED": {
                "stats": compute_stats(closed_ok),
                "positions": [pos_to_dict(p) for p in closed_ok],
            },
            "STOPPED": {
                "stats": compute_stats(stopped),
                "positions": [pos_to_dict(p) for p in stopped],
            },
        },
    }


# ── Positions by Symbol/Timeframe ───────────────────────────

@router.get("/positions-by-chart/{symbol}/{timeframe}")
async def get_positions_for_chart(
    symbol: str, 
    timeframe: str, 
    db: AsyncSession = Depends(get_db)
):
    """Get all agent positions for a specific symbol/timeframe (for chart display)."""
    from sqlalchemy import text
    
    # Normalize symbol format (BTC-USDT -> BTC/USDT)
    symbol_normalized = symbol.replace("-", "/")
    
    result = await db.execute(text("""
        SELECT p.id, p.agent_id, a.name as agent_name, p.side, 
               p.entry_price, p.stop_loss, p.take_profit, p.quantity,
               p.status, p.pnl, p.pnl_percent, p.opened_at, p.closed_at,
               p.exit_price
        FROM agent_positions p
        JOIN agents a ON p.agent_id = a.id
        WHERE p.symbol = :symbol 
          AND a.timeframe = :timeframe
        ORDER BY p.opened_at DESC
        LIMIT 50
    """), {"symbol": symbol_normalized, "timeframe": timeframe})
    
    positions = []
    for row in result.fetchall():
        positions.append({
            "id": row[0],
            "agent_id": row[1],
            "agent_name": row[2],
            "side": row[3],
            "entry_price": row[4],
            "stop_loss": row[5],
            "take_profit": row[6],
            "quantity": row[7],
            "status": row[8],
            "pnl": row[9],
            "pnl_percent": row[10],
            "opened_at": row[11].isoformat() if row[11] else None,
            "closed_at": row[12].isoformat() if row[12] else None,
            "exit_price": row[13],
        })
    
    return {"positions": positions}
