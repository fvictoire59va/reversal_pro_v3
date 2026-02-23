"""
Agent Broker API routes — CRUD agents, positions, manual close.
"""

import logging
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import (
    AgentCreate, AgentUpdate, AgentResponse, PositionResponse,
    AgentLogResponse, AgentsOverview,
)
from ..services.agent_broker import agent_broker_service
from ..services.agent_performance import get_agent_performance_data
from ..models import AgentPosition, Agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


def _build_agent_response(agent, stats: dict) -> AgentResponse:
    """Build a consistent AgentResponse from an Agent ORM object and stats dict."""
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
        confirmation_bars=getattr(agent, 'confirmation_bars', 0),
        method=getattr(agent, 'method', 'average'),
        atr_length=getattr(agent, 'atr_length', 5),
        average_length=getattr(agent, 'average_length', 5),
        absolute_reversal=getattr(agent, 'absolute_reversal', 0.5),
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        open_positions=stats["open_positions"],
        total_pnl=stats["total_pnl"],
        total_unrealized_pnl=stats["total_unrealized_pnl"],
    )


def _build_position_response(p, agent_name: str = "unknown") -> PositionResponse:
    """Build a complete PositionResponse from an AgentPosition ORM object."""
    return PositionResponse(
        id=p.id,
        agent_id=p.agent_id,
        agent_name=agent_name,
        symbol=p.symbol,
        side=p.side,
        entry_price=p.entry_price,
        exit_price=p.exit_price,
        stop_loss=p.stop_loss,
        original_stop_loss=p.original_stop_loss,
        take_profit=p.take_profit,
        tp2=p.tp2,
        quantity=p.quantity,
        original_quantity=p.original_quantity,
        status=p.status,
        partial_closed=p.partial_closed or False,
        partial_pnl=p.partial_pnl,
        pnl=p.pnl,
        pnl_percent=p.pnl_percent,
        unrealized_pnl=p.unrealized_pnl,
        unrealized_pnl_percent=p.unrealized_pnl_percent,
        current_price=p.current_price,
        pnl_updated_at=p.pnl_updated_at,
        opened_at=p.opened_at,
        closed_at=p.closed_at,
    )


# ── Agents CRUD ──────────────────────────────────────────────

@router.get("/", response_model=AgentsOverview)
async def get_agents_overview(db: AsyncSession = Depends(get_db)):
    """Get all agents with open positions and statistics."""
    agents = await agent_broker_service.get_all_agents(db)
    open_positions = await agent_broker_service.get_all_open_positions(db)

    # Single aggregate query for all agents (fixes N+1)
    stats_map = await agent_broker_service.get_all_agent_stats(db)
    empty_stats = {"open_positions": 0, "total_pnl": 0, "total_unrealized_pnl": 0}

    # Build agent responses with stats
    agent_responses = []
    for agent in agents:
        stats = stats_map.get(agent.id, empty_stats)
        agent_responses.append(_build_agent_response(agent, stats))

    # Build position responses with agent names
    agent_name_map = {a.id: a.name for a in agents}
    position_responses = [
        _build_position_response(p, agent_name_map.get(p.agent_id, "unknown"))
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


# ── Reset History (must be before /{agent_id} routes) ────────

@router.delete("/reset-history")
async def reset_agent_history(
    db: AsyncSession = Depends(get_db),
):
    """Delete skipped-signal logs and closed positions not belonging to active agents.

    Specifically removes:
      1. All TRADE_SKIPPED logs for inactive agents
      2. All TRADE_SKIPPED logs for active agents (the grey markers)
      3. All CLOSED/STOPPED positions for inactive agents
      4. Old logs (CYCLE_ERROR, etc.) for inactive agents
    """
    from sqlalchemy import text

    # 1. Get active agent IDs
    active_result = await db.execute(text(
        "SELECT id FROM agents WHERE is_active = TRUE"
    ))
    active_ids = [r[0] for r in active_result.fetchall()]

    deleted = {}

    # 2. Delete ALL TRADE_SKIPPED logs (grey markers) — they are purely informational
    res = await db.execute(text(
        "DELETE FROM agent_logs WHERE action = 'TRADE_SKIPPED'"
    ))
    deleted["skipped_logs"] = res.rowcount

    # 3. Delete closed/stopped positions NOT belonging to active agents
    if active_ids:
        res = await db.execute(text(
            "DELETE FROM agent_positions "
            "WHERE status IN ('CLOSED', 'STOPPED') "
            "  AND NOT (agent_id = ANY(:active_ids))"
        ), {"active_ids": active_ids})
    else:
        res = await db.execute(text(
            "DELETE FROM agent_positions "
            "WHERE status IN ('CLOSED', 'STOPPED')"
        ))
    deleted["closed_positions_inactive"] = res.rowcount

    # 4. Delete old logs for inactive agents (keep active agent logs)
    if active_ids:
        res = await db.execute(text(
            "DELETE FROM agent_logs "
            "WHERE NOT (agent_id = ANY(:active_ids))"
        ), {"active_ids": active_ids})
    else:
        res = await db.execute(text(
            "DELETE FROM agent_logs"
        ))
    deleted["logs_inactive_agents"] = res.rowcount

    await db.commit()

    total = sum(deleted.values())
    logger.info(f"Reset history: {deleted} (total {total} rows deleted)")

    return {
        "status": "ok",
        "deleted": deleted,
        "total_deleted": total,
    }


@router.post("/", response_model=AgentResponse)
async def create_agent(req: AgentCreate, db: AsyncSession = Depends(get_db)):
    """Create a new trading agent."""
    try:
        agent = await agent_broker_service.create_agent(
            db, req.symbol, req.timeframe, req.trade_amount, req.mode,
            req.sensitivity, req.signal_mode, req.analysis_limit,
            req.confirmation_bars, req.method, req.atr_length,
            req.average_length, req.absolute_reversal,
        )
        stats = await agent_broker_service.get_agent_stats(db, agent.id)
        return _build_agent_response(agent, stats)
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
    return _build_agent_response(agent, stats)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: int, req: AgentUpdate, db: AsyncSession = Depends(get_db)):
    """Update agent settings (trade_amount, mode, sensitivity, signal_mode, analysis_limit)."""
    agent = await agent_broker_service.get_agent(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if req.trade_amount is not None:
        agent.trade_amount = req.trade_amount
    if req.mode is not None:
        agent.mode = req.mode
    if req.sensitivity is not None:
        agent.sensitivity = req.sensitivity
    if req.signal_mode is not None:
        agent.signal_mode = req.signal_mode
    if req.analysis_limit is not None:
        agent.analysis_limit = req.analysis_limit
    if req.confirmation_bars is not None:
        agent.confirmation_bars = req.confirmation_bars
    if req.method is not None:
        agent.method = req.method
    if req.atr_length is not None:
        agent.atr_length = req.atr_length
    if req.average_length is not None:
        agent.average_length = req.average_length
    if req.absolute_reversal is not None:
        agent.absolute_reversal = req.absolute_reversal

    await db.commit()
    await db.refresh(agent)

    stats = await agent_broker_service.get_agent_stats(db, agent.id)
    return _build_agent_response(agent, stats)


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
        _build_position_response(p, name_map.get(p.agent_id, "unknown"))
        for p in positions
    ]


@router.get("/{agent_id}/positions", response_model=list[PositionResponse])
async def get_agent_positions(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Get all positions for a specific agent."""
    positions = await agent_broker_service.get_agent_positions(db, agent_id)
    agent = await agent_broker_service.get_agent(db, agent_id)
    agent_name = agent.name if agent else "unknown"

    return [
        _build_position_response(p, agent_name)
        for p in positions
    ]


@router.post("/positions/{position_id}/close", response_model=PositionResponse)
async def close_position(position_id: int, db: AsyncSession = Depends(get_db)):
    """Manually close a position."""
    pos = await agent_broker_service.close_position_manually(db, position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found or already closed")

    agent = await agent_broker_service.get_agent(db, pos.agent_id)
    return _build_position_response(pos, agent.name if agent else "unknown")


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

    stats = await agent_broker_service.get_agent_stats(db, agent_id)
    return await get_agent_performance_data(db, agent, stats)


# ── Positions by Symbol/Timeframe ───────────────────────────

@router.get("/positions-by-chart/{symbol}/{timeframe}")
async def get_positions_for_chart(
    symbol: str, 
    timeframe: str, 
    db: AsyncSession = Depends(get_db)
):
    """Get all agent positions for a specific symbol/timeframe (for chart display).
    
    Uses a two-query approach instead of correlated subqueries for performance:
      1. Fetch positions (fast JOIN, no subqueries)
      2. Batch-fetch relevant logs for matched position IDs
    """
    from sqlalchemy import text
    
    # Normalize symbol format (BTC-USDT -> BTC/USDT)
    symbol_normalized = symbol.replace("-", "/")
    
    # ── Query 1: Positions (no correlated subqueries) ──
    pos_result = await db.execute(text("""
        SELECT p.id, p.agent_id, a.name AS agent_name, p.side,
               p.entry_price, p.stop_loss, p.take_profit, p.quantity,
               p.status, p.pnl, p.pnl_percent, p.opened_at, p.closed_at,
               p.exit_price,
               p.original_stop_loss, p.tp2, p.original_quantity,
               p.partial_closed, p.partial_pnl,
               a.mode AS agent_mode
        FROM agent_positions p
        JOIN agents a ON p.agent_id = a.id
        WHERE p.symbol = :symbol
          AND a.timeframe = :timeframe
        ORDER BY p.opened_at DESC
        LIMIT 50
    """), {"symbol": symbol_normalized, "timeframe": timeframe})
    
    rows = pos_result.fetchall()
    if not rows:
        return {"positions": []}

    # Collect position IDs and agent IDs for batch log lookup
    pos_ids = [r[0] for r in rows]
    agent_ids = list({r[1] for r in rows})

    # ── Query 2: Batch-fetch all relevant logs for these positions ──
    log_result = await db.execute(text("""
        SELECT l.agent_id, l.action,
               (l.details->>'position_id')::int AS position_id,
               l.details, l.created_at
        FROM agent_logs l
        WHERE l.agent_id = ANY(:agent_ids)
          AND l.action IN ('POSITION_OPENED', 'POSITION_CLOSED', 'POSITION_STOPPED',
                           'PARTIAL_TP_CLOSED', 'BREAKEVEN_ACTIVATED')
          AND (l.details->>'position_id')::int = ANY(:pos_ids)
        ORDER BY l.created_at DESC
    """), {"agent_ids": agent_ids, "pos_ids": pos_ids})

    # Index logs by (position_id, action) — keep only the most recent per combo
    log_map: dict[tuple[int, str], dict] = {}
    for lr in log_result.fetchall():
        pid = lr[2]
        action = lr[1]
        key = (pid, action)
        if key not in log_map:
            log_map[key] = {"details": lr[3] or {}, "created_at": lr[4]}

    # ── Build response ──
    positions = []
    for row in rows:
        pid = row[0]
        agent_mode = row[19]

        close_log = log_map.get((pid, "POSITION_CLOSED")) or log_map.get((pid, "POSITION_STOPPED"))
        close_reason = close_log["details"].get("reason") if close_log else None

        open_log = log_map.get((pid, "POSITION_OPENED"))
        open_details = open_log["details"] if open_log else {}

        partial_log = log_map.get((pid, "PARTIAL_TP_CLOSED"))
        breakeven_log = log_map.get((pid, "BREAKEVEN_ACTIVATED"))

        positions.append({
            "id": pid,
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
            "close_reason": close_reason,
            "open_details": {
                "stop_loss": open_details.get("stop_loss"),
                "take_profit_1": open_details.get("take_profit_1"),
                "take_profit_2": open_details.get("take_profit_2"),
                "risk": open_details.get("risk"),
                "reward_tp1": open_details.get("reward_tp1"),
                "rr_ratio_tp1": open_details.get("rr_ratio_tp1"),
                "rr_ratio_tp2": open_details.get("rr_ratio_tp2"),
                "zone_tp_used": open_details.get("zone_tp_used"),
                "mode": open_details.get("mode") or agent_mode,
                "is_paper": open_details.get("is_paper"),
            } if open_details else {},
            "original_stop_loss": row[14],
            "tp2": row[15],
            "original_quantity": row[16],
            "partial_closed": row[17],
            "partial_pnl": row[18],
            "partial_tp_at": partial_log["created_at"].isoformat() if partial_log else None,
            "breakeven_at": breakeven_log["created_at"].isoformat() if breakeven_log else None,
        })
    
    return {"positions": positions}


# ── Skipped Signals (for chart grey markers) ────────────────

@router.get("/skipped-signals/{symbol}/{timeframe}")
async def get_skipped_signals_for_chart(
    symbol: str,
    timeframe: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all TRADE_SKIPPED logs for a specific symbol/timeframe (for chart grey markers).
    
    Returns skipped signal details so the frontend can render grey markers
    with tooltips explaining why the position was not taken.
    """
    from sqlalchemy import text

    # Normalize symbol format (BTC-USDT -> BTC/USDT)
    symbol_normalized = symbol.replace("-", "/")

    result = await db.execute(text("""
        SELECT l.id, l.agent_id, a.name as agent_name,
               l.details, l.created_at
        FROM agent_logs l
        JOIN agents a ON l.agent_id = a.id
        WHERE l.action = 'TRADE_SKIPPED'
          AND a.symbol = :symbol
          AND a.timeframe = :timeframe
          AND l.details->>'signal_time' IS NOT NULL
        ORDER BY l.created_at DESC
        LIMIT 100
    """), {"symbol": symbol_normalized, "timeframe": timeframe})

    skipped = []
    seen_keys = set()  # Deduplicate by (signal_time, side, agent_id)
    for row in result.fetchall():
        details = row[3] or {}
        signal_time = details.get("signal_time")
        side = details.get("side")
        agent_id = row[1]
        
        if not signal_time:
            continue
            
        # Deduplicate: keep only the latest skip per (signal_time, side, agent)
        dedup_key = (signal_time, side, agent_id)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        skipped.append({
            "agent_name": row[2],
            "agent_id": agent_id,
            "side": side,
            "reason": details.get("reason", "unknown"),
            "signal_time": signal_time,
            "signal_price": details.get("signal_price"),
            "entry_price": details.get("entry_price"),
            "stop_loss": details.get("stop_loss"),
            "risk_pct": details.get("risk_pct"),
            "htf_checked": details.get("htf_checked"),
            "balance": details.get("balance"),
            "position_duration_s": details.get("position_duration_s"),
            "min_gap_s": details.get("min_gap_s"),
            "skipped_at": row[4].isoformat() if row[4] else None,
        })

    return {"skipped_signals": skipped}
