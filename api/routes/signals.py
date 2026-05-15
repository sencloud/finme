"""Signals API routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("")
async def list_signals(request: Request, limit: int = Query(50, ge=1, le=500)):
    scanner = request.app.state.scanner
    store = scanner.signal_store[:limit]
    return {"signals": store, "total": len(scanner.signal_store)}


@router.get("/events")
async def list_signal_events(request: Request, limit: int = Query(100, ge=1, le=1000)):
    """Persistent event log — confirmed signals that never disappear."""
    scanner = request.app.state.scanner
    events = scanner.signal_events
    recent = events[-limit:] if len(events) > limit else events
    recent_reversed = list(reversed(recent))
    return {"events": recent_reversed, "total": len(events)}


@router.post("/scan")
async def trigger_scan(request: Request):
    scanner = request.app.state.scanner
    config = request.app.state.config
    live_engine = getattr(request.app.state, "live_engine", None)
    live_state = getattr(request.app.state, "live_state", None)
    prefixes = [w.prefix for w in config.watchlist]
    result = await scanner.scan_all(prefixes, {
        "recentBars": config.scan.recent_bars,
        "requireFinished": config.scan.require_finished,
        "includePartialTypes": config.scan.include_partial_types,
        "v14MinAlignScore": config.strategy.min_align_score,
    })
    transitions = live_engine.process_scan(result) if live_engine else []
    if live_state is not None:
        live_state["last_transition_count"] = len(transitions)
    return result


@router.get("/last")
async def last_scan(request: Request):
    scanner = request.app.state.scanner
    return scanner.last_results or {"message": "尚未执行扫描"}
