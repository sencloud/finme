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
    """触发一次同步扫描。

    可选 body:
        {"watchlist": [{"market": "futures"|"stock",
                          "prefix": str, "exchange": str, "name": str}, ...]}

    - 传 watchlist  -> 按前端给的列表扫(支持自定义品种 + A 股)
    - 不传 watchlist -> 沿用 config.yaml 中的期货 watchlist(向后兼容)
    """
    scanner = request.app.state.scanner
    config = request.app.state.config
    live_engine = getattr(request.app.state, "live_engine", None)
    live_state = getattr(request.app.state, "live_state", None)

    # 解析可选 body — 没有 body 或解析失败都按"使用 config.yaml"处理
    watchlist_payload: list[dict] | None = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            wl = body.get("watchlist")
            if isinstance(wl, list) and wl:
                # 只保留必要字段,防止前端塞奇怪的东西
                watchlist_payload = [
                    {
                        "market": str(it.get("market", "futures")),
                        "prefix": str(it.get("prefix", "")),
                        "exchange": str(it.get("exchange", "")),
                        "name": str(it.get("name", "")),
                    }
                    for it in wl
                    if isinstance(it, dict) and it.get("prefix")
                ]
    except Exception:
        watchlist_payload = None

    prefixes = [w.prefix for w in config.watchlist]
    options = {
        "recentBars": config.scan.recent_bars,
        "requireFinished": config.scan.require_finished,
        "includePartialTypes": config.scan.include_partial_types,
        "v14MinAlignScore": config.strategy.min_align_score,
    }
    result = await scanner.scan_all(prefixes, options, watchlist=watchlist_payload)
    transitions = live_engine.process_scan(result) if live_engine else []
    if live_state is not None:
        live_state["last_transition_count"] = len(transitions)
    return result


@router.get("/last")
async def last_scan(request: Request):
    scanner = request.app.state.scanner
    return scanner.last_results or {"message": "尚未执行扫描"}
