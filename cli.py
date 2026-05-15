"""Typer CLI entry point for finme-quant.

Can be run as:
  python -m finme_quant scan          (from src/python/)
  python finme_quant/cli.py scan      (from src/python/)
  python cli.py scan                  (from src/python/finme_quant/)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import typer

app = typer.Typer(name="finme", help="缠论期货交易系统", add_completion=False)


def _ensure_package_importable() -> None:
    """Make sure ``finme_quant`` is importable regardless of how the script is invoked."""
    try:
        import finme_quant  # noqa: F401
    except ImportError:
        here = os.path.dirname(os.path.abspath(__file__))
        parent = os.path.dirname(here)
        if parent not in sys.path:
            sys.path.insert(0, parent)


_ensure_package_importable()


def _load_config(config_path: str | None = None):
    from finme_quant.config import load_config
    return load_config(config_path)


def _make_tushare(cfg):
    """Create TushareService with optional local data cache."""
    from finme_quant.data.tushare_service import TushareService
    local_cache = None
    if cfg.cache.enabled:
        from finme_quant.data.local_cache import LocalDataCache
        local_cache = LocalDataCache(cfg.cache.dir)
    return TushareService(cfg.tushare.token, local_cache=local_cache)


# ======================================================================
# scan
# ======================================================================

@app.command()
def scan(
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
    prefixes: str = typer.Option("", "--prefixes", "-p", help="品种前缀列表，逗号分隔"),
    recent_bars: int = typer.Option(5, "--recent", "-r", help="最近 N 根K线内的信号"),
    realtime: bool = typer.Option(False, "--realtime", "-R", help="使用AkShare实时分钟数据"),
):
    """扫描监控品种，输出缠论信号。"""
    cfg = _load_config(config)
    from finme_quant.data.scan_service import ScanService

    ts = _make_tushare(cfg)
    rt = None
    if realtime:
        try:
            from finme_quant.data.akshare_service import AkShareService
            rt = AkShareService()
            typer.echo("[scan] AkShare实时数据源已启用")
        except Exception as e:
            typer.echo(f"[scan] AkShare不可用: {e}，使用Tushare缓存")
    scanner = ScanService(ts, realtime_source=rt)

    plist = [s.strip() for s in prefixes.split(",") if s.strip()] if prefixes else [w.prefix for w in cfg.watchlist]
    typer.echo(f"[scan] 开始扫描 {len(plist)} 个品种: {', '.join(plist)}")
    t0 = time.monotonic()

    result = asyncio.run(scanner.scan_all(plist, {"recentBars": recent_bars}))

    elapsed = time.monotonic() - t0
    typer.echo(f"[scan] 完成，耗时 {elapsed:.1f}s")

    signals = result.get("signals", [])
    if not signals:
        typer.echo("[scan] 未发现信号")
        return

    typer.echo(f"[scan] 发现 {len(signals)} 个信号:")
    for s in signals:
        direction = "做多" if s["direction"] == "long" else "做空"
        sig_date = s.get("date", "")
        typer.echo(
            f'  {s.get("displayName", "")} | {s.get("type", "")} {direction} '
            f'@ {s.get("entryPrice", s.get("price", ""))} | '
            f'时间={sig_date} | '
            f'SL={s.get("stopLoss", "")} TP={s.get("takeProfit", "")} | '
            f'组合={s.get("compositeScore", "")} '
            f'V14={s.get("v14AlignScore", "")} '
            f'信心={s.get("confidence", "")}'
        )


# ======================================================================
# helpers
# ======================================================================

def _fetch_multi_tf_data(ts, ts_code: str, start: str, end: str):
    """Fetch daily, weekly, 1h, and 15m data for backtest.

    Weekly data comes from the Tushare ``fut_weekly`` API directly.
    Minute data (1h, 15m) requires specific contract codes — we use
    ``get_dominant_minutes`` which resolves the dominant contract mapping
    and fetches across rollovers automatically.
    """
    from finme_quant.data.scan_service import ScanService

    typer.echo("[backtest] 获取日线数据...")
    daily_raw = ts.get_futures_daily(ts_code, start, end)
    if not daily_raw or len(daily_raw) < 50:
        typer.echo("[backtest] 日线数据不足 (需要至少50根K线)")
        raise typer.Exit(1)

    daily_klines = ScanService._transform_daily(daily_raw)
    typer.echo(f"[backtest] 日线: {len(daily_klines)} 根")

    typer.echo("[backtest] 获取周线数据...")
    weekly_klines: list[dict] = []
    try:
        weekly_raw = ts.get_futures_weekly(ts_code, start, end)
        weekly_klines = ScanService._transform_daily(weekly_raw) if weekly_raw else []
    except Exception as e:
        typer.echo(f"[backtest] 周线数据获取失败: {e}")
    typer.echo(f"[backtest] 周线: {len(weekly_klines)} 根")

    def _progress(code, cur, total):
        typer.echo(f"[backtest]   {code} ({cur}/{total})")

    typer.echo("[backtest] 获取1小时数据 (按主力合约轮换拉取)...")
    h1_klines: list[dict] = []
    try:
        h1_raw = ts.get_dominant_minutes(ts_code, "60min", start, end, progress_fn=_progress)
        h1_klines = ScanService._transform_minute(h1_raw) if h1_raw else []
    except Exception as e:
        typer.echo(f"[backtest] 1小时数据获取失败: {e}")
    typer.echo(f"[backtest] 1小时: {len(h1_klines)} 根")

    typer.echo("[backtest] 获取15分钟数据 (按主力合约轮换拉取)...")
    m15_klines: list[dict] = []
    try:
        m15_raw = ts.get_dominant_minutes(ts_code, "15min", start, end, progress_fn=_progress)
        m15_klines = ScanService._transform_minute(m15_raw) if m15_raw else []
    except Exception as e:
        typer.echo(f"[backtest] 15分钟数据获取失败: {e}")
    typer.echo(f"[backtest] 15分钟: {len(m15_klines)} 根")

    return daily_klines, weekly_klines, h1_klines, m15_klines


def _fetch_stock_multi_tf_data(ts, ts_code: str, start: str, end: str):
    """Fetch daily, weekly, 1h, and 15m stock data for backtest."""
    from finme_quant.data.scan_service import ScanService

    typer.echo("[backtest] 获取股票日线数据...")
    daily_raw = ts.get_stock_daily(ts_code, start, end)
    if not daily_raw or len(daily_raw) < 50:
        typer.echo("[backtest] 日线数据不足 (需要至少50根K线)")
        raise typer.Exit(1)

    daily_klines = ScanService._transform_daily(daily_raw)
    typer.echo(f"[backtest] 日线: {len(daily_klines)} 根")

    typer.echo("[backtest] 获取股票周线数据...")
    weekly_klines: list[dict] = []
    try:
        weekly_raw = ts.get_stock_weekly(ts_code, start, end)
        weekly_klines = ScanService._transform_daily(weekly_raw) if weekly_raw else []
    except Exception as e:
        typer.echo(f"[backtest] 周线数据获取失败: {e}")
    typer.echo(f"[backtest] 周线: {len(weekly_klines)} 根")

    typer.echo("[backtest] 获取股票1小时数据...")
    h1_klines: list[dict] = []
    try:
        h1_raw = ts.get_stock_minutes(ts_code, "60min", start, end)
        h1_klines = ScanService._transform_minute(h1_raw) if h1_raw else []
    except Exception as e:
        typer.echo(f"[backtest] 1小时数据获取失败: {e}")
    typer.echo(f"[backtest] 1小时: {len(h1_klines)} 根")

    typer.echo("[backtest] 获取股票15分钟数据...")
    m15_klines: list[dict] = []
    try:
        m15_raw = ts.get_stock_minutes(ts_code, "15min", start, end)
        m15_klines = ScanService._transform_minute(m15_raw) if m15_raw else []
    except Exception as e:
        typer.echo(f"[backtest] 15分钟数据获取失败: {e}")
    typer.echo(f"[backtest] 15分钟: {len(m15_klines)} 根")

    return daily_klines, weekly_klines, h1_klines, m15_klines


def _build_multi_period_context(analyzer, daily_klines, weekly_klines,
                                h1_klines, m15_klines, tag: str = "backtest") -> dict:
    """Run Chanlun analysis on each timeframe and assemble the multiPeriod context."""
    multi_period: dict = {}

    if weekly_klines and len(weekly_klines) >= 10:
        typer.echo(f"[{tag}]   分析周线...")
        multi_period["1w"] = {"result": analyzer.analyze(weekly_klines)}

    typer.echo(f"[{tag}]   分析日线...")
    multi_period["1d"] = {"result": analyzer.analyze(daily_klines)}

    if h1_klines and len(h1_klines) >= 10:
        typer.echo(f"[{tag}]   分析1小时...")
        multi_period["1h"] = {"result": analyzer.analyze(h1_klines)}

    if m15_klines and len(m15_klines) >= 10:
        typer.echo(f"[{tag}]   分析15分钟...")
        multi_period["15m"] = {"result": analyzer.analyze(m15_klines)}

    return multi_period


def _run_single_backtest(ts, cfg, prefix: str, start: str, end: str,
                         tag: str = "backtest",
                         signal_types: list[str] | None = None,
                         hour: bool = False) -> dict | None:
    """Run a single-instrument backtest and return result dict, or None on failure."""
    from finme_quant.data.futures_specs import get_spec_by_prefix, get_industry_by_prefix
    from finme_quant.core.analyzer import ChanlunAnalyzer
    from finme_quant.strategy.backtest_engine import BacktestEngine
    from finme_quant.data.scan_service import to_tushare_exchange

    spec = get_spec_by_prefix(prefix)
    if not spec:
        typer.echo(f"[{tag}] 未找到 {prefix} 的合约规格，跳过")
        return None

    industry = get_industry_by_prefix(prefix)
    exchange = spec["exchange"]
    ts_code = f"{prefix}.{to_tushare_exchange(exchange)}"

    try:
        daily_raw = ts.get_futures_daily(ts_code, start, end)
        if not daily_raw or len(daily_raw) < 50:
            typer.echo(f"[{tag}] {prefix} 日线数据不足，跳过")
            return None

        from finme_quant.data.scan_service import ScanService
        daily_klines = ScanService._transform_daily(daily_raw)
        typer.echo(f"[{tag}]   日线: {len(daily_klines)} 根")

        weekly_klines: list[dict] = []
        try:
            weekly_raw = ts.get_futures_weekly(ts_code, start, end)
            weekly_klines = ScanService._transform_daily(weekly_raw) if weekly_raw else []
        except Exception:
            pass
        typer.echo(f"[{tag}]   周线: {len(weekly_klines)} 根")

        def _progress(code, cur, total):
            typer.echo(f"[{tag}]     {code} ({cur}/{total})")

        h1_klines: list[dict] = []
        try:
            h1_raw = ts.get_dominant_minutes(ts_code, "60min", start, end, progress_fn=_progress)
            h1_klines = ScanService._transform_minute(h1_raw) if h1_raw else []
        except Exception:
            pass
        typer.echo(f"[{tag}]   1小时: {len(h1_klines)} 根")

        m15_klines: list[dict] = []
        if not hour:
            try:
                m15_raw = ts.get_dominant_minutes(ts_code, "15min", start, end, progress_fn=_progress)
                m15_klines = ScanService._transform_minute(m15_raw) if m15_raw else []
            except Exception:
                pass
            typer.echo(f"[{tag}]   15分钟: {len(m15_klines)} 根")

        entry_klines = h1_klines if hour else m15_klines
        entry_label = "1小时" if hour else "15分钟"
        if not entry_klines or len(entry_klines) < 50:
            typer.echo(f"[{tag}] {prefix} {entry_label}数据不足，跳过")
            return None

        analyzer = ChanlunAnalyzer()
        typer.echo(f"[{tag}]   运行缠论分析...")
        multi_period = _build_multi_period_context(
            analyzer, daily_klines, weekly_klines, h1_klines,
            m15_klines if not hour else None, tag=tag,
        )

        multiplier = spec["multiplier"]
        commission = spec["commission"]
        engine = BacktestEngine()
        params = {
            "initialCapital": 1_000_000,
            "contractMultiplier": multiplier,
            "commissionPerLot": commission,
            "v14StopATR": cfg.strategy.stop_atr,
            "v14TargetATR": cfg.strategy.target_atr,
            "v14TrailATR": cfg.strategy.trail_atr,
            "v14MaxHoldBars": cfg.strategy.max_hold_bars,
            "v14Cooldown": cfg.strategy.cooldown,
            "v14MinAlignScore": cfg.strategy.min_align_score,
            "v14Preset": cfg.strategy.preset,
        }
        if hour:
            params["v14EntryTimeframe"] = "1h"
        if signal_types:
            params["v14SignalTypes"] = signal_types

        result = engine.run(entry_klines, params, {"multiPeriod": multi_period})
        if result.get("error"):
            typer.echo(f"[{tag}] {prefix} 回测失败: {result['error']}")
            return None

        trades = result.get("trades", [])
        summary = result.get("summary", {})
        typer.echo(f"[{tag}]   完成: {len(trades)} 笔交易, PnL={summary.get('netProfit', 0):,.0f}")

        return {
            "prefix": prefix,
            "exchange": exchange,
            "name": spec.get("name", prefix),
            "spec": spec,
            "industry": industry,
            "backtest_result": result,
        }

    except Exception as e:
        typer.echo(f"[{tag}] {prefix} 回测异常: {e}")
        return None


_INDEX_KEYWORDS = {"HS300", "ZZ500", "ZZ1000", "SZ50", "CYB", "KC50"}


def _resolve_stock_codes(cfg, stock_input: str) -> list[str]:
    """Resolve stock input to a list of ts_codes.

    Supports:
      - Single code: ``000001.SZ``
      - Comma-separated: ``000001.SZ,600036.SH``
      - Index keyword: ``HS300``, ``ZZ500``, ``ZZ1000``, ``SZ50``
      - ``ALL``: all currently listed A-share stocks
    """
    upper = stock_input.strip().upper()
    if upper == "ALL":
        ts = _make_tushare(_load_config())
        typer.echo("[backtest] 获取全部A股上市股票...")
        rows = ts.get_all_stocks()
        codes = sorted([r["ts_code"] for r in rows if r.get("ts_code")])
        typer.echo(f"[backtest] 全市场共 {len(codes)} 只股票")
        return codes
    if upper in _INDEX_KEYWORDS:
        ts = _make_tushare(_load_config())
        typer.echo(f"[backtest] 获取 {upper} 指数成分股...")
        codes = ts.get_index_members(upper)
        typer.echo(f"[backtest] {upper} 共 {len(codes)} 只成分股")
        return codes
    return [s.strip().upper() for s in stock_input.split(",") if s.strip()]


# ======================================================================
# backtest
# ======================================================================

@app.command()
def backtest(
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
    prefix: str = typer.Option("C", "--prefix", "-p", help="品种前缀"),
    exchange: str = typer.Option("DCE", "--exchange", "-e", help="交易所"),
    start: str = typer.Option("20230101", "--start", "-s", help="起始日期 YYYYMMDD"),
    end: str = typer.Option("20241231", "--end", help="结束日期 YYYYMMDD"),
    output: str = typer.Option("", "--output", "-o", help="输出JSON文件路径"),
    all_varieties: bool = typer.Option(False, "--all", "-a", help="全市场品种逐个回测"),
    signal_types: str = typer.Option(
        "", "--signal-types", "-t",
        help="只回测指定信号类型，逗号分隔 (如 buy2,sell2)。可选: buy1,buy2,buy3,sell1,sell2,sell3,semiBuy2,semiSell2,semiBuy3,semiSell3"
    ),
    stock: str = typer.Option(
        "", "--stock",
        help="股票代码，如 000001.SZ 或 600036.SH。指定后回测股票而非期货"
    ),
    hour: bool = typer.Option(False, "--hour", help="使用1小时周期作为入场周期（默认15分钟）"),
):
    """运行V14多周期缠论回测。"""
    cfg = _load_config(config)
    type_list = [s.strip() for s in signal_types.split(",") if s.strip()] if signal_types else []

    if stock:
        stock_codes = _resolve_stock_codes(cfg, stock)
        if len(stock_codes) == 1:
            _backtest_stock(cfg, stock_codes[0], start, end, output, type_list, hour=hour)
        else:
            _backtest_stock_batch(cfg, stock_codes, start, end, output, type_list, hour=hour)
        return

    if all_varieties:
        _backtest_all(cfg, start, end, output, signal_types=type_list, hour=hour)
        return

    from finme_quant.data.futures_specs import get_spec_by_prefix
    from finme_quant.core.analyzer import ChanlunAnalyzer
    from finme_quant.strategy.backtest_engine import BacktestEngine

    from finme_quant.data.scan_service import to_tushare_exchange
    ts_code = f"{prefix}.{to_tushare_exchange(exchange)}"
    entry_tf = "1h" if hour else "15m"
    typer.echo(f"[backtest] 品种: {ts_code}, 区间: {start} - {end}, 入场周期: {entry_tf}")

    ts = _make_tushare(cfg)
    daily_klines, weekly_klines, h1_klines, m15_klines = _fetch_multi_tf_data(ts, ts_code, start, end)

    if hour:
        entry_klines = h1_klines
        if not entry_klines or len(entry_klines) < 50:
            typer.echo("[backtest] 1小时数据不足 (需要至少50根)")
            raise typer.Exit(1)
    else:
        entry_klines = m15_klines
        if not entry_klines or len(entry_klines) < 50:
            typer.echo("[backtest] 15分钟数据不足 (需要至少50根)")
            raise typer.Exit(1)

    analyzer = ChanlunAnalyzer()
    typer.echo("[backtest] 运行多周期缠论分析...")
    multi_period = _build_multi_period_context(
        analyzer, daily_klines, weekly_klines, h1_klines,
        m15_klines if not hour else None,
    )

    context = {"multiPeriod": multi_period}

    spec = get_spec_by_prefix(prefix)
    multiplier = spec["multiplier"] if spec else 10
    commission = spec["commission"] if spec else 1.21

    engine = BacktestEngine()
    params = {
        "contractMultiplier": multiplier,
        "commissionPerLot": commission,
        "v14StopATR": cfg.strategy.stop_atr,
        "v14TargetATR": cfg.strategy.target_atr,
        "v14TrailATR": cfg.strategy.trail_atr,
        "v14MaxHoldBars": cfg.strategy.max_hold_bars,
        "v14Cooldown": cfg.strategy.cooldown,
        "v14MinAlignScore": cfg.strategy.min_align_score,
        "v14Preset": cfg.strategy.preset,
    }
    if hour:
        params["v14EntryTimeframe"] = "1h"
    if type_list:
        params["v14SignalTypes"] = type_list
        typer.echo(f"[backtest] 信号过滤: 仅回测 {', '.join(type_list)}")

    typer.echo(f"[backtest] 回测引擎启动 (入场周期={entry_tf}, {len(entry_klines)} 根K线, 乘数={multiplier})...")
    result = engine.run(entry_klines, params, context)

    if result.get("error"):
        typer.echo(f"[backtest] 错误: {result['error']}")
        raise typer.Exit(1)

    _print_backtest_summary(result)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        typer.echo(f"\n结果已保存至 {output}")


def _print_backtest_summary(result: dict) -> None:
    s = result.get("summary", {})
    typer.echo("\n===== 回测结果 =====")
    typer.echo(f'策略: {s.get("strategyName", "")}')
    typer.echo(f'初始资金: {s.get("initialCapital", 0):,.0f}')
    typer.echo(f'最终资金: {s.get("finalCapital", 0):,.2f}')
    typer.echo(f'净利润: {s.get("netProfit", 0):,.2f} ({s.get("netProfitPct", 0):.1f}%)')
    typer.echo(f'总交易数: {s.get("totalTrades", 0)} (胜 {s.get("winners", 0)} / 负 {s.get("losers", 0)})')
    typer.echo(f'胜率: {s.get("winRate", 0):.1f}%')
    typer.echo(f'盈亏比: {s.get("profitFactor", 0):.2f}')
    typer.echo(f'最大回撤: {s.get("maxDrawdown", 0):,.2f} ({s.get("maxDrawdownPct", 0):.1f}%)')
    typer.echo(f'夏普比率: {s.get("sharpeRatio", 0):.2f}')
    typer.echo(f'年化收益: {s.get("annualReturn", 0):.1f}%')
    typer.echo(f'多单: {s.get("longTrades", 0)} (胜率 {s.get("longWinRate", 0):.0f}%)')
    typer.echo(f'空单: {s.get("shortTrades", 0)} (胜率 {s.get("shortWinRate", 0):.0f}%)')


def _backtest_all(cfg, start: str, end: str, output: str,
                   signal_types: list[str] | None = None,
                   hour: bool = False) -> None:
    """Run backtest for every variety in FUTURES_CONTRACT_SPECS."""
    from finme_quant.data.futures_specs import FUTURES_CONTRACT_SPECS, get_industry_by_prefix

    ts = _make_tushare(cfg)
    all_prefixes = list(FUTURES_CONTRACT_SPECS.keys())
    total = len(all_prefixes)
    entry_tf = "1h" if hour else "15m"

    typer.echo("=" * 60)
    typer.echo("  全市场品种回测")
    typer.echo("=" * 60)
    typer.echo(f"  品种数: {total}, 区间: {start} - {end}, 入场周期: {entry_tf}")
    if signal_types:
        typer.echo(f"  信号过滤: {', '.join(signal_types)}")
    typer.echo("=" * 60)

    summaries: list[dict] = []
    failed: list[str] = []
    all_results: dict[str, dict] = {}

    for i, px in enumerate(all_prefixes):
        spec = FUTURES_CONTRACT_SPECS[px]
        typer.echo(f"\n[backtest-all] === ({i + 1}/{total}) {spec['name']}({px}) ===")
        result = _run_single_backtest(ts, cfg, px, start, end, tag="backtest-all",
                                      signal_types=signal_types, hour=hour)
        if result:
            s = result["backtest_result"].get("summary", {})
            summaries.append({
                "prefix": px,
                "name": spec["name"],
                "industry": get_industry_by_prefix(px),
                "trades": s.get("totalTrades", 0),
                "winRate": s.get("winRate", 0),
                "netProfit": s.get("netProfit", 0),
                "netProfitPct": s.get("netProfitPct", 0),
                "profitFactor": s.get("profitFactor", 0),
                "maxDrawdown": s.get("maxDrawdown", 0),
                "maxDrawdownPct": s.get("maxDrawdownPct", 0),
                "sharpeRatio": s.get("sharpeRatio", 0),
                "annualReturn": s.get("annualReturn", 0),
            })
            all_results[px] = result["backtest_result"]
        else:
            failed.append(f"{spec['name']}({px})")

    if not summaries:
        typer.echo("\n[backtest-all] 没有品种成功完成回测")
        raise typer.Exit(1)

    summaries.sort(key=lambda x: x["netProfit"], reverse=True)

    typer.echo(f"\n\n{'=' * 90}")
    typer.echo(f"  全市场回测汇总 — 成功 {len(summaries)} / 失败 {len(failed)}")
    typer.echo(f"{'=' * 90}")
    typer.echo(
        f'  {"品种":>6s}  {"行业":>6s}  {"交易":>4s}  {"胜率":>6s}  '
        f'{"净利润":>10s}  {"收益%":>7s}  {"盈亏比":>6s}  '
        f'{"回撤%":>6s}  {"夏普":>5s}  {"年化%":>6s}'
    )
    typer.echo("-" * 90)
    for row in summaries:
        pf = f'{row["profitFactor"]:.2f}' if row["profitFactor"] != float("inf") else "  INF"
        typer.echo(
            f'  {row["name"]:>6s}({row["prefix"]:>3s})  '
            f'{row["industry"]:>6s}  '
            f'{row["trades"]:>4d}  '
            f'{row["winRate"]:>5.1f}%  '
            f'{row["netProfit"]:>10,.0f}  '
            f'{row["netProfitPct"]:>+6.1f}%  '
            f'{pf:>6s}  '
            f'{row["maxDrawdownPct"]:>5.1f}%  '
            f'{row["sharpeRatio"]:>5.2f}  '
            f'{row["annualReturn"]:>+5.1f}%'
        )

    if failed:
        typer.echo(f"\n  跳过/失败: {', '.join(failed)}")

    profitable = [r for r in summaries if r["netProfit"] > 0]
    if profitable:
        typer.echo(f"\n  盈利品种: {len(profitable)}/{len(summaries)}")
        top3 = profitable[:3]
        top3_strs = [f'{r["name"]}({r["prefix"]}) +{r["netProfitPct"]:.1f}%' for r in top3]
        typer.echo(f'  TOP 3: {", ".join(top3_strs)}')

    if output:
        out_data = {"summaries": summaries, "failed": failed}
        with open(output, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2, default=str)
        typer.echo(f"\n结果已保存至 {output}")


def _make_stock_params(cfg, signal_types: list[str] | None = None,
                       hour: bool = False) -> dict:
    """Build backtest params dict for A-share stocks."""
    params = {
        "initialCapital": 100_000,
        "contractMultiplier": 100,
        "commissionPerLot": 0,
        "longOnly": True,
        "t1Rule": True,
        "commissionRate": 0.00025,
        "minCommission": 5.0,
        "stampTaxRate": 0.001,
        "priceDecimals": 2,
        "v14StopATR": cfg.strategy.stop_atr,
        "v14TargetATR": cfg.strategy.target_atr,
        "v14TrailATR": cfg.strategy.trail_atr,
        "v14MaxHoldBars": cfg.strategy.max_hold_bars,
        "v14Cooldown": cfg.strategy.cooldown,
        "v14MinAlignScore": cfg.strategy.min_align_score,
        "v14Preset": cfg.strategy.preset,
    }
    if hour:
        params["v14EntryTimeframe"] = "1h"
    if signal_types:
        params["v14SignalTypes"] = signal_types
    return params


def _stock_cache_path(cache_dir: str, ts_code: str, start: str, end: str,
                      signal_types: list[str] | None = None,
                      hour: bool = False) -> str:
    """Return the cache file path for a stock backtest result."""
    from pathlib import Path
    sig_tag = "_".join(sorted(signal_types)) if signal_types else "all"
    tf_tag = "1h" if hour else "15m"
    safe_code = ts_code.replace(".", "_")
    d = Path(cache_dir) / "stock_backtest"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{safe_code}_{start}_{end}_{sig_tag}_{tf_tag}.json")


def _run_single_stock_backtest(ts, cfg, ts_code: str, start: str, end: str,
                               signal_types: list[str] | None = None,
                               tag: str = "stock-bt",
                               hour: bool = False) -> dict | None:
    """Run a single stock backtest. Returns result dict or None on failure."""
    from finme_quant.core.analyzer import ChanlunAnalyzer
    from finme_quant.strategy.backtest_engine import BacktestEngine
    from finme_quant.data.scan_service import ScanService

    cache_file = _stock_cache_path(cfg.cache.dir, ts_code, start, end, signal_types, hour=hour)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            s = cached.get("summary", {})
            typer.echo(f"[{tag}] {ts_code} 缓存命中 — {s.get('totalTrades', 0)} 笔, "
                       f"PnL={s.get('netProfit', 0):,.0f}")
            return {"ts_code": ts_code, "backtest_result": cached}
        except Exception:
            pass

    try:
        typer.echo(f"[{tag}] 获取数据 {ts_code}...")
        daily_raw = ts.get_stock_daily(ts_code, start, end)
        if not daily_raw or len(daily_raw) < 50:
            typer.echo(f"[{tag}] {ts_code} 日线不足，跳过")
            return None
        daily_klines = ScanService._transform_daily(daily_raw)

        weekly_klines: list[dict] = []
        try:
            weekly_raw = ts.get_stock_weekly(ts_code, start, end)
            weekly_klines = ScanService._transform_daily(weekly_raw) if weekly_raw else []
        except Exception:
            pass

        h1_klines: list[dict] = []
        try:
            h1_raw = ts.get_stock_minutes(ts_code, "60min", start, end)
            h1_klines = ScanService._transform_minute(h1_raw) if h1_raw else []
        except Exception:
            pass

        m15_klines: list[dict] = []
        if not hour:
            try:
                m15_raw = ts.get_stock_minutes(ts_code, "15min", start, end)
                m15_klines = ScanService._transform_minute(m15_raw) if m15_raw else []
            except Exception:
                pass

        entry_tf_label = "1H" if hour else "15m"
        entry_klines = h1_klines if hour else m15_klines
        typer.echo(f"[{tag}]   日线={len(daily_klines)} 周线={len(weekly_klines)} "
                   f"1H={len(h1_klines)}"
                   + (f" 15m={len(m15_klines)}" if not hour else ""))

        if not entry_klines or len(entry_klines) < 50:
            typer.echo(f"[{tag}] {ts_code} {entry_tf_label}数据不足，跳过")
            return None

        analyzer = ChanlunAnalyzer()
        multi_period = _build_multi_period_context(
            analyzer, daily_klines, weekly_klines, h1_klines,
            m15_klines if not hour else None, tag=tag,
        )

        engine = BacktestEngine()
        params = _make_stock_params(cfg, signal_types, hour=hour)
        result = engine.run(entry_klines, params, {"multiPeriod": multi_period})

        if result.get("error"):
            typer.echo(f"[{tag}] {ts_code} 回测失败: {result['error']}")
            return None

        trades = result.get("trades", [])
        summary = result.get("summary", {})
        typer.echo(f"[{tag}]   完成: {len(trades)} 笔交易, PnL={summary.get('netProfit', 0):,.0f}")

        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, default=str)
        except Exception:
            pass

        return {"ts_code": ts_code, "backtest_result": result}

    except Exception as e:
        typer.echo(f"[{tag}] {ts_code} 异常: {e}")
        return None


def _backtest_stock(cfg, stock_code: str, start: str, end: str,
                    output: str, signal_types: list[str] | None = None,
                    hour: bool = False) -> None:
    """Run backtest for a single stock (interactive mode with full summary)."""
    ts_code = stock_code.upper()
    entry_tf = "1h" if hour else "15m"
    typer.echo(f"[backtest] 股票: {ts_code}, 区间: {start} - {end}, 入场周期: {entry_tf}")

    ts = _make_tushare(cfg)
    result = _run_single_stock_backtest(ts, cfg, ts_code, start, end, signal_types,
                                        tag="backtest", hour=hour)

    if not result:
        typer.echo("[backtest] 回测失败")
        raise typer.Exit(1)

    _print_backtest_summary(result["backtest_result"])

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result["backtest_result"], f, ensure_ascii=False, indent=2, default=str)
        typer.echo(f"\n结果已保存至 {output}")


def _backtest_stock_batch(cfg, stock_codes: list[str], start: str, end: str,
                          output: str, signal_types: list[str] | None = None,
                          hour: bool = False) -> None:
    """Batch backtest for multiple stocks, sorted by return rate."""
    total = len(stock_codes)
    sig_desc = f", 信号={','.join(signal_types)}" if signal_types else ""
    entry_tf = "1h" if hour else "15m"

    typer.echo("=" * 70)
    typer.echo("  A股批量回测")
    typer.echo("=" * 70)
    typer.echo(f"  股票数: {total}, 区间: {start} - {end}{sig_desc}")
    typer.echo(f"  入场周期: {entry_tf}")
    typer.echo(f"  规则: 只做多 / T+1 / 佣金万2.5 / 印花税千1")
    typer.echo("=" * 70)

    ts = _make_tushare(cfg)

    summaries: list[dict] = []
    failed: list[str] = []

    for i, code in enumerate(stock_codes):
        typer.echo(f"\n[stock-bt] === ({i + 1}/{total}) {code} ===")
        result = _run_single_stock_backtest(ts, cfg, code, start, end, signal_types,
                                            tag="stock-bt", hour=hour)
        if result:
            s = result["backtest_result"].get("summary", {})
            summaries.append({
                "ts_code": code,
                "trades": s.get("totalTrades", 0),
                "winRate": s.get("winRate", 0),
                "netProfit": s.get("netProfit", 0),
                "netProfitPct": s.get("netProfitPct", 0),
                "profitFactor": s.get("profitFactor", 0),
                "maxDrawdownPct": s.get("maxDrawdownPct", 0),
                "sharpeRatio": s.get("sharpeRatio", 0),
                "annualReturn": s.get("annualReturn", 0),
            })
        else:
            failed.append(code)

    if not summaries:
        typer.echo("\n[stock-bt] 没有股票成功完成回测")
        raise typer.Exit(1)

    summaries.sort(key=lambda x: x["netProfitPct"], reverse=True)

    typer.echo(f"\n\n{'=' * 95}")
    typer.echo(f"  A股回测汇总 — 成功 {len(summaries)} / 失败 {len(failed)}")
    typer.echo(f"{'=' * 95}")
    typer.echo(
        f'  {"代码":>10s}  {"交易":>4s}  {"胜率":>6s}  '
        f'{"净利润":>10s}  {"收益%":>7s}  {"盈亏比":>6s}  '
        f'{"回撤%":>6s}  {"夏普":>5s}  {"年化%":>6s}'
    )
    typer.echo("-" * 95)
    for row in summaries:
        pf = f'{row["profitFactor"]:.2f}' if row["profitFactor"] != float("inf") else "  INF"
        typer.echo(
            f'  {row["ts_code"]:>10s}  '
            f'{row["trades"]:>4d}  '
            f'{row["winRate"]:>5.1f}%  '
            f'{row["netProfit"]:>10,.0f}  '
            f'{row["netProfitPct"]:>+6.1f}%  '
            f'{pf:>6s}  '
            f'{row["maxDrawdownPct"]:>5.1f}%  '
            f'{row["sharpeRatio"]:>5.2f}  '
            f'{row["annualReturn"]:>+5.1f}%'
        )

    if failed:
        typer.echo(f"\n  跳过/失败: {', '.join(failed[:20])}"
                   + (f" ...共{len(failed)}只" if len(failed) > 20 else ""))

    profitable = [r for r in summaries if r["netProfit"] > 0]
    typer.echo(f"\n  盈利: {len(profitable)}/{len(summaries)} 只")
    if profitable:
        top = profitable[:5]
        top_strs = [f'{r["ts_code"]} +{r["netProfitPct"]:.1f}%' for r in top]
        typer.echo(f'  TOP 5: {", ".join(top_strs)}')

    if output:
        out_data = {"summaries": summaries, "failed": failed}
        with open(output, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2, default=str)
        typer.echo(f"\n结果已保存至 {output}")


# ======================================================================
# combo-backtest
# ======================================================================

@app.command("combo-backtest")
def combo_backtest(
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
    prefixes: str = typer.Option("", "--prefixes", "-p",
                                 help="品种前缀列表，逗号分隔 (默认使用watchlist)"),
    capitals: str = typer.Option("20000,50000", "--capitals", "-k",
                                 help="资金量列表，逗号分隔"),
    max_combo: int = typer.Option(3, "--max-combo", "-m", help="最大组合品种数"),
    max_positions: int = typer.Option(3, "--max-positions", help="最大同时持仓数"),
    start: str = typer.Option("20230101", "--start", "-s", help="起始日期 YYYYMMDD"),
    end: str = typer.Option("20241231", "--end", help="结束日期 YYYYMMDD"),
    output: str = typer.Option("", "--output", "-o", help="输出JSON文件路径"),
    top_n: int = typer.Option(10, "--top", "-n", help="显示前N名组合"),
):
    """多品种组合回测，寻找不同资金量下的最优品种组合。"""
    cfg = _load_config(config)
    from finme_quant.strategy.portfolio_backtest import PortfolioBacktest

    if prefixes:
        prefix_list = [s.strip().upper() for s in prefixes.split(",") if s.strip()]
    else:
        prefix_list = [w.prefix for w in cfg.watchlist]

    capital_list = [float(s.strip()) for s in capitals.split(",") if s.strip()]

    typer.echo("=" * 60)
    typer.echo("  多品种组合回测")
    typer.echo("=" * 60)
    typer.echo(f"  品种: {', '.join(prefix_list)}")
    typer.echo(f"  资金: {', '.join(f'{c:,.0f}' for c in capital_list)}")
    typer.echo(f"  最大组合: {max_combo} 品种, 最大持仓: {max_positions}")
    typer.echo(f"  区间: {start} - {end}")
    typer.echo("=" * 60)
    typer.echo()

    ts = _make_tushare(cfg)

    instrument_results: list[dict] = []
    for i, prefix in enumerate(prefix_list):
        typer.echo(f"\n[combo] === ({i + 1}/{len(prefix_list)}) 回测 {prefix} ===")
        result = _run_single_backtest(ts, cfg, prefix, start, end, tag="combo")
        if result:
            instrument_results.append(result)

    if not instrument_results:
        typer.echo("\n[combo] 没有成功完成的品种回测")
        raise typer.Exit(1)

    typer.echo(f"\n[combo] 共 {len(instrument_results)} 个品种完成回测，开始组合分析...")

    portfolio = PortfolioBacktest()
    combo_result = portfolio.run(
        instrument_results,
        capital_list,
        max_combo_size=max_combo,
        max_positions=max_positions,
    )

    if combo_result.get("error"):
        typer.echo(f"[combo] 错误: {combo_result['error']}")
        raise typer.Exit(1)

    _display_combo_results(combo_result, capital_list, top_n)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(combo_result, f, ensure_ascii=False, indent=2, default=str)
        typer.echo(f"\n结果已保存至 {output}")


def _display_combo_results(combo_result: dict, capital_list: list[float], top_n: int):
    """Pretty-print portfolio combination results."""
    typer.echo("\n" + "=" * 70)
    typer.echo("  个体回测概况")
    typer.echo("=" * 70)
    for inst in combo_result.get("instruments", []):
        typer.echo(
            f'  {inst["name"]:>6s}({inst["prefix"]:>3s}) | '
            f'交易 {inst["totalTrades"]:>3d} 笔 | '
            f'胜率 {inst["individualWinRate"]:>5.1f}% | '
            f'PnL {inst["individualPnl"]:>10,.0f}'
        )

    typer.echo(f"\n  共分析 {combo_result.get('totalCombinations', 0)} 个组合")

    for capital in capital_list:
        capital_key = str(int(capital))
        rankings = combo_result.get("rankings", {}).get(capital_key, [])

        show_count = min(top_n, len(rankings))
        typer.echo(f"\n{'=' * 70}")
        typer.echo(f"  本金 {capital:,.0f} 元 — TOP {show_count} 组合")
        typer.echo(f"{'=' * 70}")

        if not rankings:
            typer.echo("  (无可用组合 — 资金不足以交易任何品种)")
            continue

        for rank, r in enumerate(rankings[:top_n], 1):
            combo_str = " + ".join(r["comboNames"])
            codes_str = ",".join(r["combo"])
            typer.echo(f"\n  #{rank}  {combo_str}  [{codes_str}]")
            typer.echo(
                f'      净利润: {r["netProfit"]:>10,.0f} ({r["netProfitPct"]:>+.1f}%) | '
                f'交易: {r["totalTrades"]}笔(跳过{r["skippedTrades"]}笔) | '
                f'胜率: {r["winRate"]:.1f}%'
            )
            pf_display = f'{r["profitFactor"]:.2f}' if r["profitFactor"] != float("inf") else "INF"
            typer.echo(
                f'      盈亏比: {pf_display:>6s} | '
                f'最大回撤: {r["maxDrawdown"]:>8,.0f} ({r["maxDrawdownPct"]:.1f}%) | '
                f'夏普: {r["sharpeRatio"]:.2f} | '
                f'年化: {r["annualReturn"]:.1f}%'
            )
            if r.get("byInstrument"):
                parts = []
                for p, stats in r["byInstrument"].items():
                    wr = (stats["wins"] / stats["count"] * 100) if stats["count"] > 0 else 0
                    parts.append(f'{p}:{stats["count"]}笔/{wr:.0f}%胜/{stats["pnl"]:+,.0f}')
                typer.echo(f'      明细: {" | ".join(parts)}')


# ======================================================================
# live
# ======================================================================

live_app = typer.Typer(help="实盘交易管理")
app.add_typer(live_app, name="live")


@live_app.command("start")
def live_start(
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
    auto: bool = typer.Option(False, "--auto", help="启用自动下单"),
):
    """连接CTP，启动实盘扫描与自动交易循环。"""
    cfg = _load_config(config)

    from finme_quant.trading.ctp_client import CTPClient
    from finme_quant.trading.auto_order import AutoOrderManager
    from finme_quant.data.scan_service import ScanService

    ctp = CTPClient()
    typer.echo("[live] 连接CTP...")
    result = ctp.connect(cfg.ctp.model_dump())
    if result["status"] != "connected":
        typer.echo(f"[live] CTP连接失败: {result}")
        raise typer.Exit(1)
    typer.echo(f"[live] CTP已连接 (phase={result['phase']})")

    symbols = [f"{w.prefix}.{w.exchange}" for w in cfg.watchlist]
    if symbols:
        sub = ctp.subscribe(symbols)
        typer.echo(f"[live] 已订阅 {len(sub.get('subscribed', []))} 个合约")

    auto_cfg = cfg.auto_trade.model_dump()
    auto_cfg["enabled"] = auto or cfg.auto_trade.enabled
    auto_mgr = AutoOrderManager(ctp, auto_cfg)

    ts = _make_tushare(cfg)
    scanner = ScanService(ts)

    scan_interval = cfg.scan.interval_minutes * 60
    typer.echo(f"[live] 扫描间隔: {cfg.scan.interval_minutes}分钟, 自动下单: {auto_mgr.enabled}")
    typer.echo("[live] 按 Ctrl+C 停止...")

    try:
        while True:
            try:
                prefix_list = [w.prefix for w in cfg.watchlist]
                scan_opts = {
                    "v14MinAlignScore": cfg.strategy.min_align_score,
                }
                scan_result = asyncio.run(scanner.scan_all(prefix_list, scan_opts))
                signals = scan_result.get("signals", [])
                typer.echo(f"[live] 扫描完成, 信号数: {len(signals)}")
                for s in signals:
                    direction = "做多" if s.get("direction") == "long" else "做空"
                    sig_date = s.get("date", "")
                    typer.echo(
                        f'  {s.get("displayName", "")} | {s.get("type", "")} {direction} '
                        f'@ {s.get("entryPrice", s.get("price", ""))} | '
                        f'时间={sig_date} | '
                        f'组合={s.get("compositeScore", "")} '
                        f'V14={s.get("v14AlignScore", "")} '
                        f'信心={s.get("confidence", "")}'
                    )

                if signals and auto_mgr.enabled:
                    exec_results = auto_mgr.process_signals(signals)
                    for r in exec_results:
                        typer.echo(f"[live] 信号执行: {r}")

                ticks = ctp.get_ticks()
                if ticks:
                    close_results = auto_mgr.check_sl_tp(ticks)
                    for r in close_results:
                        typer.echo(f"[live] 止损止盈: {r}")

            except Exception as e:
                typer.echo(f"[live] 扫描/执行异常: {e}")

            time.sleep(scan_interval)

    except KeyboardInterrupt:
        typer.echo("\n[live] 正在断开CTP...")
        ctp.disconnect()
        typer.echo("[live] 已断开")


@live_app.command("stop")
def live_stop():
    """发送停止信号（占位命令）。"""
    typer.echo("[live] 请在运行终端按 Ctrl+C 停止实盘循环。")


# ======================================================================
# report
# ======================================================================

report_app = typer.Typer(help="交易报告")
app.add_typer(report_app, name="report")


@report_app.command("daily")
def report_daily():
    """打印今日PnL报告。"""
    from finme_quant.trading.reporter import Reporter
    r = Reporter()
    report = r.daily_report()
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2, default=str))


@report_app.command("trades")
def report_trades(limit: int = typer.Option(50, "--limit", "-n")):
    """打印交易历史。"""
    from finme_quant.trading.reporter import Reporter
    r = Reporter()
    trades = r.trade_history(limit)
    if not trades:
        typer.echo("暂无交易记录")
    else:
        typer.echo(json.dumps(trades, ensure_ascii=False, indent=2, default=str))


# ======================================================================
# serve
# ======================================================================

@app.command()
def serve(
    config: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
    host: str = typer.Option("", "--host", "-H", help="监听地址"),
    port: int = typer.Option(0, "--port", "-P", help="监听端口"),
    live: bool = typer.Option(False, "--live", "-l", help="启用实盘模式 (连接CTP + 后台扫描循环)"),
    auto: bool = typer.Option(False, "--auto", help="启用自动下单 (需同时启用 --live)"),
    daemon: bool = typer.Option(False, "--daemon", "-d", help="后台守护进程运行"),
    pidfile: str = typer.Option("finme_serve.pid", "--pidfile", help="PID文件路径 (daemon模式)"),
    logfile: str = typer.Option("finme_serve.log", "--logfile", help="日志文件路径 (daemon模式)"),
):
    """启动HTTP服务。加 --live 同时启动实盘扫描循环。

    \b
    仅API模式:    finme serve
    实盘监控:     finme serve --live
    实盘+自动:    finme serve --live --auto
    后台运行:     finme serve --live --daemon
    指定日志:     finme serve --live -d --logfile /var/log/finme.log
    停止后台:     finme serve-stop
    """
    cfg = _load_config(config)
    h = host or cfg.api.host
    p = port or cfg.api.port

    if daemon:
        _start_daemon(config, h, p, live, auto, pidfile, logfile)
        return

    _run_serve(cfg, h, p, live, auto, logfile=None, config_path=config)


def _run_serve(cfg, h: str, p: int, live: bool, auto: bool,
               logfile: str | None, config_path: str | None = None) -> None:
    """Run the uvicorn server (called both in foreground and daemon)."""
    import logging

    if logfile:
        logging.basicConfig(
            filename=logfile, level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        log = logging.getLogger("finme")
        log.info("[serve] 后台启动 http://%s:%d live=%s auto=%s", h, p, live, auto)
    else:
        if live:
            typer.echo(f"[serve] 实盘模式 — 监控品种: {', '.join(w.name or w.prefix for w in cfg.watchlist)}")
            typer.echo(f"[serve] 扫描间隔: {cfg.scan.interval_minutes}分钟, 自动下单: {auto or cfg.auto_trade.enabled}")
        else:
            typer.echo("[serve] 纯API模式 (不连CTP, 不自动扫描)")
        typer.echo(f"[serve] 启动 http://{h}:{p}  监控页面: http://{h}:{p}/monitor")

    import uvicorn
    from finme_quant.api.server import create_app
    api_app = create_app(cfg, live=live, auto=auto, config_path=config_path)
    uvicorn.run(api_app, host=h, port=p, log_level="info")


def _start_daemon(config_path, host, port, live, auto, pidfile, logfile) -> None:
    """Fork a background daemon process (Unix) or spawn a detached process (Windows)."""
    from pathlib import Path
    pidpath = Path(pidfile).resolve()
    logpath = Path(logfile).resolve()

    if pidpath.exists():
        old_pid = pidpath.read_text().strip()
        if old_pid and _pid_alive(int(old_pid)):
            typer.echo(f"[serve] 已有后台进程运行中 (PID {old_pid})，先执行 serve-stop 停止")
            raise typer.Exit(1)
        pidpath.unlink(missing_ok=True)

    if sys.platform == "win32":
        _daemon_windows(config_path, host, port, live, auto, pidpath, logpath)
    else:
        _daemon_unix(config_path, host, port, live, auto, pidpath, logpath)


def _daemon_unix(config_path, host, port, live, auto, pidpath, logpath) -> None:
    """Double-fork daemon for Linux/macOS."""
    import signal

    pid = os.fork()
    if pid > 0:
        typer.echo(f"[serve] 后台进程已启动 (PID {pid})")
        typer.echo(f"[serve] PID文件: {pidpath}")
        typer.echo(f"[serve] 日志文件: {logpath}")
        typer.echo(f"[serve] 停止命令: finme serve-stop --pidfile {pidpath}")
        return

    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    sys.stdout.flush()
    sys.stderr.flush()
    devnull = open(os.devnull, "r")
    log_fd = open(logpath, "a", encoding="utf-8")
    os.dup2(devnull.fileno(), sys.stdin.fileno())
    os.dup2(log_fd.fileno(), sys.stdout.fileno())
    os.dup2(log_fd.fileno(), sys.stderr.fileno())

    pidpath.write_text(str(os.getpid()))

    def _cleanup(signum, frame):
        pidpath.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    cfg = _load_config(config_path)
    _run_serve(cfg, host, port, live, auto, logfile=str(logpath),
               config_path=config_path)


def _daemon_windows(config_path, host, port, live, auto, pidpath, logpath) -> None:
    """Spawn a detached subprocess on Windows."""
    import subprocess

    cmd = [sys.executable, "-m", "finme_quant.cli", "serve",
           "--host", host, "--port", str(port)]
    if config_path:
        cmd += ["--config", config_path]
    if live:
        cmd.append("--live")
    if auto:
        cmd.append("--auto")
    cmd += ["--logfile", str(logpath)]

    creation_flags = 0
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    except AttributeError:
        pass

    with open(logpath, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
        )

    pidpath.write_text(str(proc.pid))
    typer.echo(f"[serve] 后台进程已启动 (PID {proc.pid})")
    typer.echo(f"[serve] PID文件: {pidpath}")
    typer.echo(f"[serve] 日志文件: {logpath}")
    typer.echo(f"[serve] 停止命令: finme serve-stop --pidfile {pidpath}")


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


@app.command("serve-stop")
def serve_stop(
    pidfile: str = typer.Option("finme_serve.pid", "--pidfile", help="PID文件路径"),
):
    """停止后台运行的 serve 进程。"""
    import signal
    from pathlib import Path

    pidpath = Path(pidfile).resolve()
    if not pidpath.exists():
        typer.echo(f"[serve-stop] PID文件不存在: {pidpath}")
        raise typer.Exit(1)

    pid_str = pidpath.read_text().strip()
    if not pid_str:
        pidpath.unlink(missing_ok=True)
        typer.echo("[serve-stop] PID文件为空，已删除")
        return

    pid = int(pid_str)
    if not _pid_alive(pid):
        pidpath.unlink(missing_ok=True)
        typer.echo(f"[serve-stop] 进程 {pid} 已不存在，清理PID文件")
        return

    typer.echo(f"[serve-stop] 正在停止进程 {pid}...")
    if sys.platform == "win32":
        os.kill(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGTERM)

    for _ in range(30):
        time.sleep(0.5)
        if not _pid_alive(pid):
            break

    if _pid_alive(pid):
        typer.echo(f"[serve-stop] SIGTERM 未生效，强制终止...")
        if sys.platform == "win32":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL)

    pidpath.unlink(missing_ok=True)
    typer.echo(f"[serve-stop] 进程 {pid} 已停止")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
