"""
Agent performance service — computes hierarchical performance statistics.

Extracted from routes/agents.py to keep route handlers thin.
"""

from collections import defaultdict
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, AgentPosition


def _compute_stats(pos_list: list) -> dict:
    """Build stats summary from a list of AgentPosition objects."""
    if not pos_list:
        return {
            "count": 0, "closed_count": 0, "open_count": 0,
            "pnl": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "avg_pnl": 0, "best": 0, "worst": 0, "avg_duration_min": 0,
        }

    closed = [p for p in pos_list if p.status in ("CLOSED", "STOPPED")]
    open_count = sum(1 for p in pos_list if p.status == "OPEN")
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
        "open_count": open_count,
        "pnl": round(total_pnl, 4),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 4),
        "best": round(best, 4),
        "worst": round(worst, 4),
        "avg_duration_min": round(avg_dur, 1),
    }


def _pos_to_dict(p) -> dict:
    """Convert an AgentPosition ORM object to a plain dict."""
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


async def get_agent_performance_data(
    db: AsyncSession, agent: Agent, stats: dict,
) -> dict:
    """
    Build the full hierarchical performance tree for an agent.

    Returns a dict with: agent info, summary, by_side, by_date, by_status.
    """
    # Fetch ALL positions for this agent
    result = await db.execute(
        select(AgentPosition)
        .where(AgentPosition.agent_id == agent.id)
        .order_by(AgentPosition.opened_at.desc())
    )
    positions = list(result.scalars().all())

    # ── Group by side ──
    long_positions = [p for p in positions if p.side == "LONG"]
    short_positions = [p for p in positions if p.side == "SHORT"]

    # ── Group by date ──
    by_date_map: dict[str, list] = defaultdict(list)
    for p in positions:
        if p.opened_at:
            paris_date = p.opened_at.strftime("%Y-%m-%d")
            by_date_map[paris_date].append(p)

    date_nodes = []
    for date_str in sorted(by_date_map.keys(), reverse=True):
        day_positions = by_date_map[date_str]
        date_nodes.append({
            "date": date_str,
            "stats": _compute_stats(day_positions),
            "positions": [_pos_to_dict(p) for p in day_positions],
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
            **_compute_stats(positions),
            "total_pnl": stats["total_pnl"],
            "unrealized_pnl": stats["total_unrealized_pnl"],
        },
        "by_side": {
            "LONG": {
                "stats": _compute_stats(long_positions),
                "positions": [_pos_to_dict(p) for p in long_positions],
            },
            "SHORT": {
                "stats": _compute_stats(short_positions),
                "positions": [_pos_to_dict(p) for p in short_positions],
            },
        },
        "by_date": date_nodes,
        "by_status": {
            "OPEN": {
                "stats": _compute_stats(open_pos),
                "positions": [_pos_to_dict(p) for p in open_pos],
            },
            "CLOSED": {
                "stats": _compute_stats(closed_ok),
                "positions": [_pos_to_dict(p) for p in closed_ok],
            },
            "STOPPED": {
                "stats": _compute_stats(stopped),
                "positions": [_pos_to_dict(p) for p in stopped],
            },
        },
    }
