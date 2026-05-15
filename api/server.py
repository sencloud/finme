"""FastAPI application factory with optional live trading loop."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ..config import AppConfig, patch_auto_trade_enabled, resolve_config_path
from .routes import report, signals, positions, backtest

logger = logging.getLogger(__name__)


def create_app(config: AppConfig | None = None, *,
               live: bool = False, auto: bool = False,
               config_path: str | Path | None = None) -> FastAPI:
    if config is None:
        from ..config import load_config
        config = load_config(config_path)

    resolved_config_path = resolve_config_path(config_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        _init_services(application, config, live=live, auto=auto,
                       config_path=resolved_config_path)

        scan_task = None
        if live:
            scan_task = asyncio.create_task(
                _live_scan_loop(application, config))

        yield

        if scan_task and not scan_task.done():
            scan_task.cancel()
            try:
                await scan_task
            except asyncio.CancelledError:
                pass

        ctp = getattr(application.state, "ctp_client", None)
        if ctp and ctp.connected:
            logger.info("[live] 正在断开CTP...")
            ctp.disconnect()

    app = FastAPI(
        title="Finme Quant API", version="0.2.0",
        description="缠论期货交易系统 API (实盘监控)",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = config
    app.state.config_path = resolved_config_path
    app.state.live_mode = live
    app.state.auto_trade_enabled = bool(auto or config.auto_trade.enabled)

    app.include_router(report.router, prefix="/api/report", tags=["report"])
    app.include_router(signals.router, prefix="/api/signals", tags=["signals"])
    app.include_router(positions.router, prefix="/api", tags=["positions"])
    app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])

    _register_api_routes(app)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        @app.get("/monitor")
        async def monitor_page():
            html_path = static_dir / "monitor.html"
            if html_path.exists():
                return FileResponse(str(html_path), media_type="text/html")
            return {"error": "monitor.html not found"}

        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


def _register_api_routes(app: FastAPI) -> None:
    """Register top-level API endpoints on the app."""

    @app.get("/api/status")
    async def status():
        ctp = getattr(app.state, "ctp_client", None)
        scanner = getattr(app.state, "scanner", None)
        live_engine = getattr(app.state, "live_engine", None)
        live_state = getattr(app.state, "live_state", {})
        auto_order = getattr(app.state, "auto_order", None)
        auto_trade_on = bool(auto_order.enabled) if auto_order else bool(
            getattr(app.state, "auto_trade_enabled", False))
        return {
            "ctp": ctp.get_status() if ctp else {"connected": False},
            "scanner": scanner.progress if scanner else None,
            "config_loaded": True,
            "live_mode": getattr(app.state, "live_mode", False),
            "auto_trade": auto_trade_on,
            "auto_trade_can_toggle": auto_order is not None,
            "live": live_state,
            "signal_events_count": len(scanner.signal_events) if scanner else 0,
            "tracked_positions": len(live_engine.active_positions) if live_engine else 0,
            "execution_enabled": False,
            "execution_signal_count": 0,
        }

    @app.post("/api/auto-trade/toggle")
    async def toggle_auto_trade(request: Request):
        payload = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        auto_order = getattr(app.state, "auto_order", None)
        cfg = getattr(app.state, "config", None)
        if auto_order is None or cfg is None:
            return {"ok": False, "error": "auto_order未初始化"}

        if "enabled" in payload:
            target = bool(payload.get("enabled"))
        else:
            target = not auto_order.enabled

        auto_order.set_enabled(target)
        cfg.auto_trade.enabled = target
        app.state.auto_trade_enabled = target

        logger.info("[api] 自动下单开关 -> %s", "开启" if target else "关闭")
        return {"ok": True, "enabled": target}

    @app.get("/api/watchlist")
    async def watchlist():
        cfg = getattr(app.state, "config", None)
        if not cfg:
            return {"watchlist": []}
        return {
            "watchlist": [
                {"prefix": w.prefix, "exchange": w.exchange, "name": w.name}
                for w in cfg.watchlist
            ],
            "scan_interval_minutes": cfg.scan.interval_minutes,
            "strategy_preset": cfg.strategy.preset,
            "entry_timeframe": cfg.strategy.entry_timeframe,
        }

    @app.get("/api/session-info")
    async def session_info():
        from ..strategy.chanlun_combo import (
            SESSION_CLOSE_BARS, SESSION_OPEN_BARS,
            NIGHT_SESSION_PRODUCTS, check_signal_tradability,
        )
        now = datetime.now()
        current_check = check_signal_tradability(now)
        return {
            "currentTime": now.isoformat(),
            "currentTradeable": current_check["tradeable"],
            "currentNote": current_check["reason"],
            "sessionCloseBars": {str(k): v for k, v in SESSION_CLOSE_BARS.items()},
            "sessionOpenBars": {str(k): v for k, v in SESSION_OPEN_BARS.items()},
            "nightSessions": NIGHT_SESSION_PRODUCTS,
        }

    @app.get("/api/account")
    async def account():
        ctp = getattr(app.state, "ctp_client", None)
        if not ctp or not ctp.connected:
            return {"account": None, "connected": False}
        return {"account": ctp.get_account(), "connected": True}

    @app.get("/api/live/orders")
    async def live_orders():
        auto_mgr = getattr(app.state, "auto_order", None)
        return {"orders": auto_mgr.order_log if auto_mgr else []}

    @app.get("/api/live/pending-orders")
    async def live_pending_orders():
        auto_mgr = getattr(app.state, "auto_order", None)
        if auto_mgr is None or not hasattr(auto_mgr, "pending_orders"):
            return {"pending": []}
        return {"pending": auto_mgr.pending_orders.items}

    @app.post("/api/live/pending-orders/flush")
    async def flush_pending_orders():
        auto_mgr = getattr(app.state, "auto_order", None)
        if auto_mgr is None or not hasattr(auto_mgr, "flush_pending"):
            return {"ok": False, "error": "auto_order未初始化"}
        try:
            results = auto_mgr.flush_pending()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "dispatched": len(results), "results": results}

    @app.get("/api/trades")
    async def trades():
        live_engine = getattr(app.state, "live_engine", None)
        if not live_engine:
            return {"trades": [], "active": [], "closed": [],
                    "decisions": []}
        return {
            "trades": live_engine.all_trades_for_display,
            "active": [_pos_summary(p) for p in live_engine.active_positions],
            "closed": [_trade_summary(t) for t in live_engine.closed_trades],
            "decisions": live_engine.decision_log,
        }

    @app.get("/api/live/state")
    async def live_state_rows():
        live_engine = getattr(app.state, "live_engine", None)
        return {"rows": live_engine.state_rows if live_engine else []}

    @app.get("/api/execution/signals")
    async def execution_signals_last():
        """Deprecated: strict backtest parity mode no longer emits execution-layer signals."""
        return {"signals": []}


def _pos_summary(p: dict) -> dict:
    return {k: p.get(k) for k in (
        "varietyCode", "displayName", "symbol", "contract", "direction",
        "entryPrice", "entryTime", "stopLoss", "takeProfit",
        "compositeScore", "v14AlignScore", "signalType", "signalFamily",
        "isTrailing", "highestPrice", "lowestPrice",
        "source", "rule", "status", "holdMinutes",
        "liveOpened", "liveStatus", "liveOrderId", "liveReason",
        "liveTradedVolume", "volume",
    )}


def _trade_summary(t: dict) -> dict:
    return {k: t.get(k) for k in (
        "varietyCode", "displayName", "symbol", "contract", "direction",
        "entryPrice", "entryTime", "exitPrice", "exitTime",
        "exitReason", "pnl", "grossPnl", "commission", "contractMultiplier",
        "volume", "holdMinutes",
        "compositeScore", "v14AlignScore", "signalType", "signalFamily",
        "source", "rule", "status",
        "liveOpened", "liveStatus", "liveOrderId", "liveReason",
        "liveCloseStatus", "liveCloseOrderId", "liveCloseReason",
    )}


def _init_services(app: FastAPI, config: AppConfig, *,
                   live: bool = False, auto: bool = False,
                   config_path: Path | None = None) -> None:
    from ..data.tushare_service import TushareService
    from ..data.local_cache import LocalDataCache
    from ..data.scan_service import ScanService
    from ..trading.ctp_client import CTPClient
    from ..trading.auto_order import AutoOrderManager
    from ..trading.live_engine import LiveTradingEngine
    from ..trading.reporter import Reporter

    local_cache = LocalDataCache(config.cache.dir) if config.cache.enabled else None
    ts = TushareService(config.tushare.token, local_cache=local_cache)
    ctp = CTPClient()

    realtime_source = None
    if live:
        try:
            from ..data.akshare_service import AkShareService
            realtime_source = AkShareService()
            logger.info("[init] AkShare实时数据源已启用")
        except Exception as e:
            logger.warning("[init] AkShare不可用，将使用Tushare缓存: %s", e)

    scanner = ScanService(ts, realtime_source=realtime_source)
    reporter = Reporter(ctp)

    auto_cfg = config.auto_trade.model_dump()
    auto_cfg["enabled"] = bool(auto or config.auto_trade.enabled)

    cfg_path_resolved = config_path or resolve_config_path()

    def _persist_auto_trade_enabled(flag: bool) -> None:
        try:
            patch_auto_trade_enabled(cfg_path_resolved, flag)
        except Exception as exc:
            logger.error("[init] 写回 auto_trade.enabled 失败: %s", exc)

    auto_order = AutoOrderManager(
        ctp, auto_cfg, persist_fn=_persist_auto_trade_enabled,
    )
    live_engine = LiveTradingEngine(config, executor=auto_order)

    app.state.tushare = ts
    app.state.ctp_client = ctp
    app.state.scanner = scanner
    app.state.reporter = reporter
    app.state.auto_order = auto_order
    app.state.live_engine = live_engine
    app.state.execution_last_signals = []
    app.state.live_state = {
        "running": False,
        "scan_count": 0,
        "last_scan_at": None,
        "last_signal_count": 0,
        "last_transition_count": 0,
        "errors": [],
        "ctp_connected": False,
    }

    if live:
        logger.info("[live] 连接CTP...")
        try:
            result = ctp.connect(config.ctp.model_dump())
            if result["status"] == "connected":
                app.state.live_state["ctp_connected"] = True
                logger.info("[live] CTP已连接 (phase=%s)", result["phase"])

                symbols = [f"{w.prefix}.{w.exchange}" for w in config.watchlist]
                if symbols:
                    sub = ctp.subscribe(symbols)
                    logger.info("[live] 已订阅 %d 个合约", len(sub.get("subscribed", [])))
            else:
                err = f"CTP连接失败: {result}"
                logger.warning("[live] %s — 将以纯扫描模式运行", err)
                app.state.live_state["errors"].append(err)
        except Exception as e:
            err = f"CTP连接异常: {e}"
            logger.error("[live] %s — 将以纯扫描模式运行", err)
            app.state.live_state["errors"].append(err)


async def _live_scan_loop(app: FastAPI, config: AppConfig) -> None:
    """Background coroutine: periodically scan watchlist and drive live state."""
    scanner = app.state.scanner
    live_engine = app.state.live_engine
    ctp = app.state.ctp_client
    auto_order = getattr(app.state, "auto_order", None)
    live_state = app.state.live_state

    scan_interval = config.scan.interval_minutes * 60
    prefix_list = [w.prefix for w in config.watchlist]
    scan_opts = {
        "recentBars": config.scan.recent_bars,
        "requireFinished": config.scan.require_finished,
        "includePartialTypes": config.scan.include_partial_types,
        "v14MinAlignScore": config.strategy.min_align_score,
    }

    live_state["running"] = True
    logger.info("[live] 后台扫描循环启动 — %d个品种, 间隔%d秒",
                len(prefix_list), scan_interval)

    await asyncio.sleep(2)

    while True:
        try:
            # Flush conditional orders that were queued while the market
            # was closed. Runs before the scan so freshly-opened positions
            # are visible to the exit-evaluation logic on this very tick.
            if auto_order is not None:
                try:
                    results = auto_order.flush_pending()
                    if results:
                        logger.info("[live] 条件单派发 %d 条", len(results))
                except Exception as exc:
                    logger.error("[live] 条件单派发失败: %s", exc)

            scan_result = await scanner.scan_all(prefix_list, scan_opts)
            sigs = scan_result.get("signals", [])
            live_state["scan_count"] += 1
            live_state["last_scan_at"] = datetime.now().isoformat()
            live_state["last_signal_count"] = len(sigs)

            confirmed = [s for s in sigs if s.get("confirmed", False)]
            provisional = [s for s in sigs if not s.get("confirmed", False)]
            logger.info("[live] 第%d轮扫描完成, 信号: %d (确认: %d, 待确认: %d), 历史事件: %d",
                        live_state["scan_count"], len(sigs),
                        len(confirmed), len(provisional),
                        len(scanner.signal_events))

            for s in sigs:
                direction = "做多" if s.get("direction") == "long" else "做空"
                status_tag = "确认" if s.get("confirmed") else "待确认"
                logger.info(
                    "  [%s] %s | %s %s @ %s | 评分=%s V14=%s 信心=%s",
                    status_tag,
                    s.get("displayName", ""),
                    s.get("type", ""), direction,
                    s.get("entryPrice", s.get("price", "")),
                    s.get("compositeScore", ""),
                    s.get("v14AlignScore", ""),
                    s.get("confidence", ""),
                )

            app.state.execution_last_signals = []
            live_state["last_execution_count"] = 0

            transitions = live_engine.process_scan(scan_result)
            live_state["last_transition_count"] = len(transitions)
            for item in transitions:
                logger.info("[live] 状态迁移: %s %s",
                            item.get("varietyCode"), item.get("event"))

        except asyncio.CancelledError:
            live_state["running"] = False
            logger.info("[live] 扫描循环已停止")
            raise
        except Exception as e:
            err_msg = f"扫描异常: {e}"
            logger.error("[live] %s", err_msg)
            live_state["errors"].append(err_msg)
            if len(live_state["errors"]) > 50:
                live_state["errors"] = live_state["errors"][-20:]

        await asyncio.sleep(scan_interval)
