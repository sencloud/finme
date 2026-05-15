"""Backtest API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class BacktestRequest(BaseModel):
    prefix: str = "C"
    exchange: str = "DCE"
    start_date: str = "20230101"
    end_date: str = "20241231"
    preset: str = "balanced"
    initial_capital: float = 100000
    stop_atr: float = 1.5
    target_atr: float = 3.0
    trail_atr: float = 1.0
    max_hold_bars: int = 40
    cooldown: int = 2
    min_align_score: int = 25


@router.post("/run")
async def run_backtest(request: Request, body: BacktestRequest):
    from finme_quant.data.scan_service import ScanService, to_tushare_exchange
    from finme_quant.data.futures_specs import get_spec_by_prefix
    from finme_quant.core.analyzer import ChanlunAnalyzer
    from finme_quant.strategy.backtest_engine import BacktestEngine

    config = request.app.state.config
    ts = request.app.state.tushare
    ts_code = f"{body.prefix}.{to_tushare_exchange(body.exchange)}"

    daily_raw = ts.get_futures_daily(ts_code, body.start_date, body.end_date)
    if not daily_raw or len(daily_raw) < 50:
        return {"error": "日线数据不足，至少需要50根K线", "trades": []}

    daily_klines = ScanService._transform_daily(daily_raw)

    weekly_klines: list[dict] = []
    try:
        weekly_raw = ts.get_futures_weekly(ts_code, body.start_date, body.end_date)
        weekly_klines = ScanService._transform_daily(weekly_raw) if weekly_raw else []
    except Exception:
        pass

    h1_klines: list[dict] = []
    try:
        h1_raw = ts.get_dominant_minutes(ts_code, "60min", body.start_date, body.end_date)
        h1_klines = ScanService._transform_minute(h1_raw) if h1_raw else []
    except Exception:
        pass

    m15_klines: list[dict] = []
    try:
        m15_raw = ts.get_dominant_minutes(ts_code, "15min", body.start_date, body.end_date)
        m15_klines = ScanService._transform_minute(m15_raw) if m15_raw else []
    except Exception:
        pass

    if not m15_klines or len(m15_klines) < 50:
        return {"error": "15分钟数据不足，无法进行V14回测", "trades": []}

    analyzer = ChanlunAnalyzer()
    multi_period: dict = {}

    if weekly_klines and len(weekly_klines) >= 10:
        multi_period["1w"] = {"result": analyzer.analyze(weekly_klines)}
    multi_period["1d"] = {"result": analyzer.analyze(daily_klines)}
    if h1_klines and len(h1_klines) >= 10:
        multi_period["1h"] = {"result": analyzer.analyze(h1_klines)}
    multi_period["15m"] = {"result": analyzer.analyze(m15_klines)}

    context = {"multiPeriod": multi_period}

    spec = get_spec_by_prefix(body.prefix)
    multiplier = spec["multiplier"] if spec else 10
    commission = spec["commission"] if spec else 1.21

    engine = BacktestEngine()
    params = {
        "initialCapital": body.initial_capital,
        "contractMultiplier": multiplier,
        "commissionPerLot": commission,
        "v14StopATR": body.stop_atr,
        "v14TargetATR": body.target_atr,
        "v14TrailATR": body.trail_atr,
        "v14MaxHoldBars": body.max_hold_bars,
        "v14Cooldown": body.cooldown,
        "v14MinAlignScore": body.min_align_score,
        "v14Preset": body.preset,
    }

    return engine.run(m15_klines, params, context)
