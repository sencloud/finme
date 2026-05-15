"""Self-contained live position state machine.

This module is **the** authoritative model for an open trading position.
It deliberately has zero knowledge of:

- :class:`finme_quant.strategy.backtest_engine.BacktestEngine` internals
- chanlun / snapshot logic
- CTP order routing

A :class:`LivePosition` is created once at fill-time with frozen risk
parameters (entry price, initial stop-loss, take-profit, trailing distance,
ATR at entry, max-hold-bars). On every subsequent scan it is fed the
freshest K-line series and decides—based purely on its own params and the
real bars—whether the position has hit a stop / target / trailing exit /
timeout. The state machine owns its mutable runtime state (running highs /
lows, ratcheted trailing stop, last-evaluated bar) and is fully
JSON-serialisable so it can survive process restarts.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field, fields
from typing import Optional


# ---------------------------------------------------------------------------
# Public exit reasons (kept in Chinese to match the existing decision_log
# / UI rendering that look for substrings like "止损" / "目标止盈").
# ---------------------------------------------------------------------------
EXIT_STOP_LOSS = "止损"
EXIT_TAKE_PROFIT = "目标止盈"
EXIT_TRAILING = "跟踪止盈"
EXIT_TIMEOUT = "超时平仓"


@dataclass
class ExitInfo:
    """Outcome of an exit-eval that triggered."""

    exitReason: str
    exitTriggerLevel: float  # the simulated level (e.g. exact stop price)
    exitBarClose: float      # close of the bar that triggered the exit
    exitBarTime: int         # bar timestamp (millis)
    exitDate: str            # bar's date string
    exitIndex: int           # index within the bar series passed in
    highestPrice: float
    lowestPrice: float
    isTrailing: bool


@dataclass
class LivePosition:
    """Self-contained state machine for one open trading position.

    Field naming intentionally mirrors the camelCase used elsewhere in this
    project (and exposed through the FastAPI surface) so that
    :py:meth:`to_display_dict` can be a near-identity transform.
    """

    # ---------------- Identity --------------------------------------------
    varietyCode: str
    symbol: str
    contract: str
    displayName: str
    direction: str  # "long" / "short"

    # ---------------- Entry (frozen) --------------------------------------
    entryTime: str        # bar's date string e.g. "2026-04-28 23:00:00"
    entryBarStamp: int    # bar timestamp in millis
    entryPrice: float

    # ---------------- Frozen risk parameters ------------------------------
    initialStopLoss: float
    takeProfit: float
    trailDistance: float
    trailActivation: float
    maxHoldBars: int
    entryAtr: float

    # ---------------- Strategy metadata for UI / decisions ---------------
    signalType: str = ""
    signalFamily: str = ""
    strategyType: str = ""
    alignScore: float = 0.0
    trendContext: str = ""
    reason: str = ""

    # ---------------- Mutable running state -------------------------------
    stopLoss: float = 0.0
    highestPrice: float = 0.0
    lowestPrice: float = 0.0
    isTrailing: bool = False
    lastEvaluatedBarStamp: int = 0
    holdBarsAtLastEval: int = 0

    # ---------------- Live execution status -------------------------------
    liveOpened: bool = False
    liveStatus: str = "tracking"
    liveOrderId: Optional[str] = None
    liveReason: str = ""
    liveTradedVolume: Optional[float] = None
    volume: int = 1

    # ---------------- Misc ------------------------------------------------
    priceDecimals: int = 0
    extra: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------
    # Lifecycle helpers
    # ---------------------------------------------------------------------

    def __post_init__(self) -> None:
        if self.stopLoss == 0.0 and self.initialStopLoss:
            self.stopLoss = self.initialStopLoss
        if self.lastEvaluatedBarStamp == 0:
            self.lastEvaluatedBarStamp = self.entryBarStamp
        if self.highestPrice == 0.0:
            self.highestPrice = self.entryPrice
        if self.lowestPrice == 0.0:
            self.lowestPrice = self.entryPrice

    # ---------------------------------------------------------------------
    # Identity / serialization
    # ---------------------------------------------------------------------

    def state_key(self) -> str:
        """Stable identifier for this trade across scans."""
        return "|".join([
            self.varietyCode,
            self.entryTime,
            self.direction,
            self.strategyType,
            f"{self.entryPrice}",
        ])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LivePosition":
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in valid})

    def to_display_dict(self) -> dict:
        """Frontend-friendly dict matching the old API contract."""
        d = self.to_dict()
        # Drop fields the UI never asked for to keep payloads tidy.
        d.pop("extra", None)
        d.pop("priceDecimals", None)
        d.pop("holdBarsAtLastEval", None)
        # Aliases preserved for backward compatibility with the existing UI.
        d["directionCode"] = self.direction
        d["direction"] = "做多" if self.direction == "long" else "做空"
        d["entryBarTime"] = self.entryTime  # legacy field was a date string
        d["compositeScore"] = self.alignScore
        d["v14AlignScore"] = self.alignScore
        d["source"] = "backtest_v14"
        d["rule"] = ""
        d["stateKey"] = self.state_key()
        d["entryKey"] = self.state_key()
        return d

    # ---------------------------------------------------------------------
    # Exit evaluation — the heart of the state machine
    # ---------------------------------------------------------------------

    def evaluate_exit(self, bars: list[dict],
                      atr_series: list[float]) -> Optional[ExitInfo]:
        """Walk K-lines newer than ``lastEvaluatedBarStamp`` and decide exit.

        Returns an :class:`ExitInfo` if an exit triggers; otherwise mutates
        the running state in place and returns ``None``. Re-evaluating the
        last seen bar is intentional: in real-time the most recent bar's
        high / low can keep updating between scans.
        """
        if not bars or self.entryBarStamp == 0:
            return None

        entry_idx = -1
        last_idx = -1
        for i, bar in enumerate(bars):
            bt = bar.get("time", 0)
            if entry_idx < 0 and bt == self.entryBarStamp:
                entry_idx = i
            if bt == self.lastEvaluatedBarStamp:
                last_idx = i
        if entry_idx < 0:
            # Entry bar slid out of the data window; without it we can't
            # compute hold_bars. Caller should warn.
            return None
        if last_idx < 0:
            last_idx = entry_idx

        fallback_atr = self.entryAtr or 1.0
        # Re-evaluate the last seen bar to capture intra-bar updates.
        start_idx = max(last_idx, entry_idx + 1)

        for i in range(start_idx, len(bars)):
            bar = bars[i]
            cur_atr = (atr_series[i]
                       if i < len(atr_series) and atr_series[i] > 0
                       else fallback_atr)

            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])

            self.highestPrice = max(self.highestPrice, high)
            self.lowestPrice = min(self.lowestPrice, low)
            hold_bars = i - entry_idx

            # 1) Timeout (highest priority — guarantees we never overstay)
            if self.maxHoldBars > 0 and hold_bars >= self.maxHoldBars:
                return self._make_exit(EXIT_TIMEOUT, self._rp(close), bar, i)

            if self.direction == "long":
                # 2) Hard stop
                if low <= self.stopLoss:
                    return self._make_exit(EXIT_STOP_LOSS, self.stopLoss, bar, i)
                # 3) Trailing
                if self.trailDistance > 0:
                    if (not self.isTrailing
                            and (high - self.entryPrice) >= self.trailActivation):
                        self.isTrailing = True
                    if self.isTrailing:
                        ts = self._rp(self.highestPrice - self.trailDistance)
                        if ts > self.entryPrice and close <= ts:
                            return self._make_exit(EXIT_TRAILING, ts, bar, i)
                        ratchet = self._rp(self.highestPrice
                                           - self.trailDistance * 1.5)
                        self.stopLoss = max(self.stopLoss, ratchet)
                # 4) Target
                if high >= self.takeProfit:
                    return self._make_exit(EXIT_TAKE_PROFIT, self.takeProfit, bar, i)
                # 5) Lock-in profit after ≥5 bars
                if hold_bars >= 5:
                    profit = close - self.entryPrice
                    if profit > cur_atr * 0.5:
                        lock = self._rp(self.entryPrice + profit * 0.3)
                        self.stopLoss = max(self.stopLoss, lock)
            else:  # short
                if high >= self.stopLoss:
                    return self._make_exit(EXIT_STOP_LOSS, self.stopLoss, bar, i)
                if self.trailDistance > 0:
                    if (not self.isTrailing
                            and (self.entryPrice - low) >= self.trailActivation):
                        self.isTrailing = True
                    if self.isTrailing:
                        ts = self._rp(self.lowestPrice + self.trailDistance)
                        if ts < self.entryPrice and close >= ts:
                            return self._make_exit(EXIT_TRAILING, ts, bar, i)
                        ratchet = self._rp(self.lowestPrice
                                           + self.trailDistance * 1.5)
                        self.stopLoss = min(self.stopLoss, ratchet)
                if low <= self.takeProfit:
                    return self._make_exit(EXIT_TAKE_PROFIT, self.takeProfit, bar, i)
                if hold_bars >= 5:
                    profit = self.entryPrice - close
                    if profit > cur_atr * 0.5:
                        lock = self._rp(self.entryPrice - profit * 0.3)
                        self.stopLoss = min(self.stopLoss, lock)

            self.holdBarsAtLastEval = hold_bars

        # No exit triggered; persist the high-water mark.
        self.lastEvaluatedBarStamp = bars[-1].get("time",
                                                  self.lastEvaluatedBarStamp)
        return None

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _make_exit(self, reason: str, trigger_level: float,
                   bar: dict, bar_idx: int) -> ExitInfo:
        return ExitInfo(
            exitReason=reason,
            exitTriggerLevel=float(trigger_level),
            exitBarClose=float(bar.get("close", trigger_level)),
            exitBarTime=int(bar.get("time", 0)),
            exitDate=bar.get("date", ""),
            exitIndex=bar_idx,
            highestPrice=self.highestPrice,
            lowestPrice=self.lowestPrice,
            isTrailing=self.isTrailing,
        )

    def _rp(self, value: float) -> float:
        return round(float(value), self.priceDecimals)
