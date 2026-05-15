"""Portfolio combination backtester.

Runs individual backtests for multiple instruments, then evaluates all
combinations under different capital levels to find optimal portfolios.

The approach:
1. Each instrument is backtested independently to produce a trade list.
2. For each combination of instruments and each capital level, trades
   are replayed chronologically with shared capital, margin constraints,
   and position limits.
3. Combinations are ranked by net profit, Sharpe ratio, and other metrics.
"""

from __future__ import annotations

import itertools
import math
from datetime import datetime

MARGIN_RATE_MAP = {
    "农产品": 0.09,
    "黑色系": 0.13,
    "有色金属": 0.12,
    "能源化工": 0.12,
    "贵金属": 0.10,
    "金融期货": 0.15,
    "航运": 0.15,
}

DEFAULT_MARGIN_RATE = 0.12


def estimate_margin(entry_price: float, multiplier: float,
                    industry: str = "", rate_override: float | None = None) -> float:
    rate = rate_override or MARGIN_RATE_MAP.get(industry, DEFAULT_MARGIN_RATE)
    return entry_price * multiplier * rate


class PortfolioBacktest:
    """Evaluate multi-instrument portfolio combinations."""

    def __init__(self, margin_rate_override: float | None = None):
        self.margin_rate_override = margin_rate_override

    def run(
        self,
        instrument_results: list[dict],
        capitals: list[float],
        max_combo_size: int = 3,
        max_positions: int = 3,
    ) -> dict:
        """Run portfolio combination analysis.

        Parameters
        ----------
        instrument_results : list of dict
            Each dict: {prefix, exchange, name, spec, industry, backtest_result}
        capitals : list of float
            Capital levels to test (e.g. [20000, 50000]).
        max_combo_size : int
            Maximum number of instruments in a single combination.
        max_positions : int
            Maximum concurrent open positions across the portfolio.

        Returns
        -------
        dict with keys: instruments, rankings (per capital), totalCombinations
        """
        instruments = self._prepare_instruments(instrument_results)

        if not instruments:
            return {"error": "没有有效的回测结果", "instruments": [], "rankings": {}, "totalCombinations": 0}

        prefixes = [inst["prefix"] for inst in instruments]
        inst_map = {inst["prefix"]: inst for inst in instruments}

        all_combos: list[tuple[str, ...]] = []
        for size in range(1, min(max_combo_size, len(prefixes)) + 1):
            for combo in itertools.combinations(prefixes, size):
                all_combos.append(combo)

        rankings: dict[str, list[dict]] = {}
        for capital in capitals:
            combo_results = []
            for combo in all_combos:
                combo_instruments = [inst_map[p] for p in combo]
                result = self._simulate_portfolio(combo_instruments, capital, max_positions)
                if result["totalTrades"] > 0:
                    combo_results.append({
                        "combo": list(combo),
                        "comboNames": [inst_map[p]["name"] for p in combo],
                        **result,
                    })

            combo_results.sort(
                key=lambda r: (r["netProfit"], r["sharpeRatio"]),
                reverse=True,
            )
            rankings[str(int(capital))] = combo_results

        return {
            "instruments": [{
                "prefix": inst["prefix"],
                "name": inst["name"],
                "totalTrades": len(inst["trades"]),
                "individualPnl": inst["summary"].get("netProfit", 0),
                "individualWinRate": inst["summary"].get("winRate", 0),
            } for inst in instruments],
            "rankings": rankings,
            "totalCombinations": len(all_combos),
        }

    def _prepare_instruments(self, instrument_results: list[dict]) -> list[dict]:
        instruments = []
        for inst in instrument_results:
            trades = inst["backtest_result"].get("trades", [])
            if not trades:
                continue
            spec = inst["spec"]
            industry = inst.get("industry", "")
            margin_rate = self.margin_rate_override or MARGIN_RATE_MAP.get(industry, DEFAULT_MARGIN_RATE)

            annotated_trades = []
            for t in trades:
                margin = estimate_margin(t["entryPrice"], spec["multiplier"], industry, self.margin_rate_override)
                annotated_trades.append({
                    **t,
                    "prefix": inst["prefix"],
                    "instrumentName": inst.get("name", inst["prefix"]),
                    "margin": margin,
                    "contractMultiplier": spec["multiplier"],
                })

            instruments.append({
                "prefix": inst["prefix"],
                "name": inst.get("name", inst["prefix"]),
                "exchange": inst["exchange"],
                "trades": annotated_trades,
                "margin_rate": margin_rate,
                "spec": spec,
                "summary": inst["backtest_result"].get("summary", {}),
            })
        return instruments

    def _simulate_portfolio(
        self,
        instruments: list[dict],
        initial_capital: float,
        max_positions: int,
    ) -> dict:
        events = self._build_event_timeline(instruments)
        if not events:
            return self._empty_metrics(initial_capital)

        capital = initial_capital
        open_positions: dict[str, dict] = {}
        open_margins: dict[str, float] = {}
        taken_trades: list[dict] = []
        skipped_trades = 0
        equity_points: list[dict] = [{"date": "start", "value": initial_capital}]
        peak = initial_capital
        max_dd = 0.0
        max_dd_pct = 0.0

        for event in events:
            if event["type"] == "open":
                if len(open_positions) >= max_positions:
                    skipped_trades += 1
                    continue
                has_instrument = any(
                    t.get("prefix") == event["prefix"] for t in open_positions.values()
                )
                if has_instrument:
                    skipped_trades += 1
                    continue
                used_margin = sum(open_margins.values())
                available = capital - used_margin
                if event["margin"] > available * 0.9:
                    skipped_trades += 1
                    continue
                if event["margin"] > capital * 0.8:
                    skipped_trades += 1
                    continue

                open_positions[event["trade_id"]] = event["trade"]
                open_margins[event["trade_id"]] = event["margin"]

            elif event["type"] == "close":
                if event["trade_id"] not in open_positions:
                    continue

                del open_positions[event["trade_id"]]
                del open_margins[event["trade_id"]]
                capital += event["pnl"]

                taken_trades.append({
                    **event["trade"],
                    "capitalAfter": capital,
                })

                if capital > peak:
                    peak = capital
                dd = peak - capital
                if dd > max_dd:
                    max_dd = dd
                    max_dd_pct = (dd / peak * 100) if peak > 0 else 0

                equity_points.append({"date": event["date"], "value": capital})

                if capital <= 0:
                    break

        return self._compute_metrics(taken_trades, skipped_trades, equity_points,
                                     initial_capital, capital, max_dd, max_dd_pct)

    def _build_event_timeline(self, instruments: list[dict]) -> list[dict]:
        events = []
        for inst in instruments:
            for idx, trade in enumerate(inst["trades"]):
                trade_id = f"{inst['prefix']}_{idx}"
                events.append({
                    "type": "open",
                    "date": trade["entryDate"],
                    "trade_id": trade_id,
                    "prefix": inst["prefix"],
                    "margin": trade["margin"],
                    "trade": trade,
                })
                events.append({
                    "type": "close",
                    "date": trade.get("exitDate", trade["entryDate"]),
                    "trade_id": trade_id,
                    "prefix": inst["prefix"],
                    "margin": trade["margin"],
                    "pnl": trade["pnl"],
                    "trade": trade,
                })
        events.sort(key=lambda e: (e["date"], 0 if e["type"] == "close" else 1))
        return events

    def _compute_metrics(self, taken_trades, skipped_trades, equity_points,
                         initial_capital, final_capital, max_dd, max_dd_pct) -> dict:
        total = len(taken_trades)
        if total == 0:
            return self._empty_metrics(initial_capital)

        winners = [t for t in taken_trades if t["pnl"] > 0]
        losers = [t for t in taken_trades if t["pnl"] < 0]
        net_profit = final_capital - initial_capital
        gross_profit = sum(t["pnl"] for t in winners)
        gross_loss = abs(sum(t["pnl"] for t in losers))

        win_rate = (len(winners) / total * 100) if total > 0 else 0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0
        )
        avg_win = gross_profit / len(winners) if winners else 0
        avg_loss = gross_loss / len(losers) if losers else 0

        sharpe = self._calc_sharpe(equity_points)

        first_date = taken_trades[0].get("entryDate", "")
        last_date = taken_trades[-1].get("exitDate", taken_trades[-1].get("entryDate", ""))
        days = self._date_diff_days(first_date, last_date)
        years = max(days / 365, 0.1)
        annual_return = (net_profit / initial_capital / years) * 100

        by_instrument: dict[str, dict] = {}
        for t in taken_trades:
            p = t.get("prefix", "?")
            if p not in by_instrument:
                by_instrument[p] = {"count": 0, "wins": 0, "pnl": 0.0}
            by_instrument[p]["count"] += 1
            if t["pnl"] > 0:
                by_instrument[p]["wins"] += 1
            by_instrument[p]["pnl"] += t["pnl"]

        return {
            "initialCapital": initial_capital,
            "finalCapital": final_capital,
            "netProfit": net_profit,
            "netProfitPct": (net_profit / initial_capital * 100) if initial_capital > 0 else 0,
            "totalTrades": total,
            "skippedTrades": skipped_trades,
            "winners": len(winners),
            "losers": len(losers),
            "winRate": win_rate,
            "grossProfit": gross_profit,
            "grossLoss": gross_loss,
            "profitFactor": profit_factor,
            "avgWin": avg_win,
            "avgLoss": avg_loss,
            "maxDrawdown": max_dd,
            "maxDrawdownPct": max_dd_pct,
            "sharpeRatio": sharpe,
            "annualReturn": annual_return,
            "byInstrument": by_instrument,
        }

    def _empty_metrics(self, initial_capital: float) -> dict:
        return {
            "initialCapital": initial_capital,
            "finalCapital": initial_capital,
            "netProfit": 0,
            "netProfitPct": 0,
            "totalTrades": 0,
            "skippedTrades": 0,
            "winners": 0,
            "losers": 0,
            "winRate": 0,
            "grossProfit": 0,
            "grossLoss": 0,
            "profitFactor": 0,
            "avgWin": 0,
            "avgLoss": 0,
            "maxDrawdown": 0,
            "maxDrawdownPct": 0,
            "sharpeRatio": 0,
            "annualReturn": 0,
            "byInstrument": {},
        }

    @staticmethod
    def _calc_sharpe(equity_points: list[dict]) -> float:
        if len(equity_points) < 2:
            return 0.0
        daily_map: dict[str, float] = {}
        for e in equity_points:
            day = (e.get("date") or "")[:10]
            if day and day != "start":
                daily_map[day] = e["value"]
        vals = list(daily_map.values())
        if len(vals) < 2:
            return 0.0
        returns = []
        for i in range(1, len(vals)):
            if vals[i - 1] == 0:
                continue
            returns.append((vals[i] - vals[i - 1]) / vals[i - 1])
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    @staticmethod
    def _date_diff_days(date1: str, date2: str) -> float:
        try:
            d1 = datetime.fromisoformat(str(date1).replace(" ", "T")[:10])
            d2 = datetime.fromisoformat(str(date2).replace(" ", "T")[:10])
            return max(float((d2 - d1).days), 1.0)
        except Exception:
            return 365.0
