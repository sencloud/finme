"""Trading report generation.

Computes daily PnL, equity curves, trade journals, and summary statistics.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime


class Reporter:
    def __init__(self, ctp_client=None) -> None:
        self.ctp = ctp_client
        self._trade_journal: list[dict] = []
        self._equity_snapshots: list[dict] = []

    def record_trade(self, trade: dict) -> None:
        self._trade_journal.append({
            **trade,
            "recorded_at": datetime.now().isoformat(),
        })

    def snapshot_equity(self, capital: float, unrealized: float = 0) -> None:
        self._equity_snapshots.append({
            "date": date.today().isoformat(),
            "datetime": datetime.now().isoformat(),
            "capital": capital,
            "unrealized": unrealized,
            "total": capital + unrealized,
        })

    def daily_report(self, target_date: date | None = None) -> dict:
        d = (target_date or date.today()).isoformat()
        day_trades = [t for t in self._trade_journal if t.get("datetime", "").startswith(d)]

        total_pnl = sum(t.get("pnl", 0) for t in day_trades)
        winners = [t for t in day_trades if t.get("pnl", 0) > 0]
        losers = [t for t in day_trades if t.get("pnl", 0) < 0]

        account = self.ctp.get_account() if self.ctp and self.ctp.connected else None

        return {
            "date": d,
            "totalTrades": len(day_trades),
            "totalPnl": total_pnl,
            "winners": len(winners),
            "losers": len(losers),
            "winRate": (len(winners) / len(day_trades) * 100) if day_trades else 0,
            "grossProfit": sum(t["pnl"] for t in winners),
            "grossLoss": abs(sum(t["pnl"] for t in losers)),
            "trades": day_trades,
            "account": account,
        }

    def equity_curve(self) -> list[dict]:
        return list(self._equity_snapshots)

    def trade_history(self, limit: int = 50) -> list[dict]:
        return self._trade_journal[-limit:]

    def summary_stats(self) -> dict:
        trades = self._trade_journal
        if not trades:
            return {"totalTrades": 0, "netPnl": 0}

        total = len(trades)
        winners = [t for t in trades if t.get("pnl", 0) > 0]
        losers = [t for t in trades if t.get("pnl", 0) < 0]
        gross_profit = sum(t["pnl"] for t in winners)
        gross_loss = abs(sum(t["pnl"] for t in losers))

        by_variety: dict = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
        for t in trades:
            v = t.get("varietyCode", t.get("symbol", "unknown"))
            by_variety[v]["count"] += 1
            by_variety[v]["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                by_variety[v]["wins"] += 1

        by_month: dict = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
        for t in trades:
            m = (t.get("datetime") or "")[:7]
            by_month[m]["count"] += 1
            by_month[m]["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                by_month[m]["wins"] += 1

        avg_win = gross_profit / len(winners) if winners else 0
        avg_loss = gross_loss / len(losers) if losers else 0

        return {
            "totalTrades": total,
            "netPnl": gross_profit - gross_loss,
            "grossProfit": gross_profit,
            "grossLoss": gross_loss,
            "winRate": len(winners) / total * 100 if total else 0,
            "profitFactor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "avgWin": avg_win,
            "avgLoss": avg_loss,
            "byVariety": dict(by_variety),
            "byMonth": dict(by_month),
            "maxDrawdown": self._calc_max_drawdown(),
        }

    def _calc_max_drawdown(self) -> float:
        if not self._equity_snapshots:
            return 0.0
        peak = self._equity_snapshots[0]["total"]
        max_dd = 0.0
        for s in self._equity_snapshots:
            v = s["total"]
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
        return max_dd
