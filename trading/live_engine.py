"""Live trading engine — fully decoupled architecture.

The previous design used :class:`BacktestEngine.snapshot()` as the source of
truth for *both* "is there a fresh entry signal?" and "should this live
position close right now?". Because the chanlun analysis underlying the
snapshot is path-dependent, every new bar could retroactively shift the
detected buy/sell points and force a spurious close on already-open trades.

This rewrite imposes a clean separation:

1. **Snapshot is a signal source ONLY.** It is consulted exclusively when
   the engine has no live position for a given variety, to discover a fresh
   entry trigger.

2. **Live position state is owned by :class:`LivePosition`.** Once filled,
   a position evolves entirely on real K-lines via its own
   stop-loss / take-profit / trailing / timeout machinery. Snapshot drift
   cannot mutate it.

3. **Persistence is durable.** Active positions plus per-variety scan state
   are atomically written to ``data/live_state.json`` after each scan, and
   reloaded on startup so trailing-stop history survives restarts.

4. **Reconciliation with CTP** runs lazily: if a persisted live position is
   not present in CTP holdings (and CTP is reachable), it is treated as
   externally closed and archived; orphan CTP positions are warned about
   but not auto-adopted.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..data.futures_specs import get_spec_by_prefix
from ..strategy.backtest_engine import BacktestEngine
from .live_position import (
    EXIT_STOP_LOSS, EXIT_TAKE_PROFIT, EXIT_TIMEOUT, EXIT_TRAILING,
    ExitInfo, LivePosition,
)
from .position_store import PositionStore

logger = logging.getLogger(__name__)

_MAX_DECISION_LOG = 400
_MAX_CLOSED_TRADES = 500
_MAX_CONSUMED_SIGNALS = 64   # per variety — keeps JSON small
_DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "live_state.json"


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LiveTradingEngine:
    """State machine that drives live trading from periodic scan results."""

    def __init__(self, config, executor=None,
                 state_path: str | Path | None = None) -> None:
        self.config = config
        self.executor = executor
        self.backtest = BacktestEngine()

        self._active_positions: dict[str, LivePosition] = {}
        self._closed_trades: list[dict] = []
        self._decision_log: list[dict] = []
        self._variety_state: dict[str, dict] = {}
        # Set of ``entryBarStamp`` values per variety that have already been
        # opened (filled, queued, skipped, or archived). Used to prevent the
        # snapshot from re-proposing the same historical bsPoint every scan
        # after a stop-loss triggers and the skipped_no_live_open archive
        # removes the in-memory position.
        self._consumed_signals: dict[str, set[int]] = {}

        self._store = PositionStore(state_path or _DEFAULT_STATE_PATH)
        self._reconciled = False
        self._load_persisted_state()

    # ------------------------------------------------------------------
    # Public API (read-only views)
    # ------------------------------------------------------------------

    def process_scan(self, scan_result: dict) -> list[dict]:
        # Reconcile against CTP once (the first scan after process start).
        if not self._reconciled:
            self._reconcile_with_ctp()
            self._reconciled = True

        transitions: list[dict] = []
        dirty = False
        for row in (scan_result.get("results") or []):
            prefix = row.get("prefix", "")
            if not prefix:
                continue
            try:
                row_transitions = self._process_variety(row)
                if row_transitions:
                    dirty = True
                transitions.extend(row_transitions)
            except Exception as exc:
                logger.exception("[LiveEngine] %s 处理失败: %s", prefix, exc)
                state = self._state_for(prefix, row)
                state["lastError"] = str(exc)
                state["lastTransition"] = "scan_error"

        # Even if no transitions fired, evaluate-exit may have ratcheted a
        # trailing stop or advanced ``lastEvaluatedBarStamp`` — always save.
        self._persist()
        if dirty:
            logger.debug("[LiveEngine] 持仓快照已落盘 (%s)", self._store.path)
        return transitions

    @property
    def active_positions(self) -> list[dict]:
        result = []
        for pos in self._active_positions.values():
            item = pos.to_display_dict()
            item["status"] = self._open_status_label(pos)
            item["holdMinutes"] = _minutes_since(pos.entryTime)
            result.append(item)
        result.sort(key=lambda x: x.get("entryTime", ""), reverse=True)
        return result

    @property
    def closed_trades(self) -> list[dict]:
        result = []
        for trade in self._closed_trades:
            item = dict(trade)
            if item.get("exitTime"):
                item = self._with_net_pnl(item.get("varietyCode", ""), item)
                item["holdMinutes"] = _minutes_between(
                    item.get("entryTime", ""),
                    item.get("exitTime", ""),
                )
            result.append(item)
        return result

    @property
    def all_trades_for_display(self) -> list[dict]:
        result = [*self.active_positions, *self.closed_trades]
        result.sort(key=lambda t: t.get("exitTime") or t.get("entryTime") or "",
                    reverse=True)
        return result

    @property
    def decision_log(self) -> list[dict]:
        return list(self._decision_log)

    @property
    def state_rows(self) -> list[dict]:
        rows = []
        for prefix, state in self._variety_state.items():
            pos = self._active_positions.get(prefix)
            display = pos.to_display_dict() if pos else None
            if display is not None:
                display["status"] = self._open_status_label(pos)
                display["holdMinutes"] = _minutes_since(pos.entryTime)
            rows.append({
                "varietyCode": prefix,
                "displayName": state.get("displayName", prefix),
                "entryTimeframe": state.get("entryTimeframe", "15m"),
                "latestBarTime": state.get("latestBarTime"),
                "lastProcessedBarTime": state.get("lastProcessedBarTime"),
                "initialized": state.get("initialized", False),
                "lastTransition": state.get("lastTransition", ""),
                "lastError": state.get("lastError", ""),
                "openPosition": deepcopy(display),
            })
        rows.sort(key=lambda x: x.get("displayName", x["varietyCode"]))
        return rows

    # ==================================================================
    # Persistence
    # ==================================================================

    def _load_persisted_state(self) -> None:
        try:
            payload = self._store.load()
        except Exception as exc:
            logger.error("[LiveEngine] 加载持仓快照失败: %s", exc)
            return

        positions = payload.get("positions") or {}
        if positions:
            self._active_positions.update(positions)
            for prefix, pos in positions.items():
                logger.info(
                    "[LiveEngine] 恢复持仓 %s: %s @ %s stop=%s target=%s "
                    "trailing=%s lastEval=%s",
                    prefix, pos.direction, pos.entryPrice,
                    pos.stopLoss, pos.takeProfit, pos.isTrailing,
                    pos.lastEvaluatedBarStamp,
                )

        for prefix, state_blob in (payload.get("varietyState") or {}).items():
            base = self._blank_variety_state(prefix, state_blob.get("displayName", prefix))
            base.update({k: v for k, v in state_blob.items() if k in base})
            # Once we have any persisted state for a variety, treat it as
            # initialized so the first scan after restart immediately runs
            # exit-eval rather than re-bootstrapping from snapshot.
            if prefix in self._active_positions:
                base["initialized"] = True
            self._variety_state[prefix] = base

        for prefix, stamps in (payload.get("consumedSignals") or {}).items():
            self._consumed_signals[prefix] = set(int(s) for s in (stamps or []) if s)

    def _persist(self) -> None:
        # Trim the per-variety state to JSON-friendly fields only (drop
        # things like in-memory error tracebacks).
        compact_state: dict[str, dict] = {}
        for prefix, state in self._variety_state.items():
            compact_state[prefix] = {
                "displayName": state.get("displayName", prefix),
                "entryTimeframe": state.get("entryTimeframe", "15m"),
                "initialized": bool(state.get("initialized", False)),
                "lastProcessedBarStamp": int(state.get("lastProcessedBarStamp") or 0),
                "lastProcessedBarTime": state.get("lastProcessedBarTime"),
                "latestBarTime": state.get("latestBarTime"),
                "lastTransition": state.get("lastTransition", ""),
                "lastError": state.get("lastError", ""),
            }
        try:
            self._store.save(self._active_positions, compact_state,
                             consumed_signals=self._consumed_signals)
        except Exception as exc:
            logger.error("[LiveEngine] 持仓快照写入失败: %s", exc)

    # ------------------------------------------------------------------
    # Consumed-signal tracking (prevents reopen loops on the same bsPoint)
    # ------------------------------------------------------------------

    def _mark_signal_consumed(self, prefix: str, entry_bar_stamp: int) -> None:
        if not entry_bar_stamp:
            return
        bucket = self._consumed_signals.setdefault(prefix, set())
        bucket.add(int(entry_bar_stamp))
        if len(bucket) > _MAX_CONSUMED_SIGNALS:
            # Keep the most recent stamps only.
            trimmed = sorted(bucket)[-_MAX_CONSUMED_SIGNALS:]
            self._consumed_signals[prefix] = set(trimmed)

    def _is_signal_consumed(self, prefix: str, entry_bar_stamp: int) -> bool:
        if not entry_bar_stamp:
            return False
        return int(entry_bar_stamp) in self._consumed_signals.get(prefix, ())

    # ==================================================================
    # Reconciliation against CTP holdings
    # ==================================================================

    def _reconcile_with_ctp(self) -> None:
        ctp = getattr(self.executor, "ctp", None) if self.executor else None
        if not ctp or not getattr(ctp, "ready", False):
            if self._active_positions:
                logger.info(
                    "[LiveEngine] CTP 未就绪，暂跳过持仓对齐 — 已加载 %d 个本地持仓",
                    len(self._active_positions),
                )
            return

        try:
            ctp_positions = ctp.get_positions() or []
        except Exception as exc:
            logger.warning("[LiveEngine] 拉取CTP持仓失败 (%s) — 跳过对齐", exc)
            return

        ctp_index = {self._normalize_position_key(p): p for p in ctp_positions}

        # Scenario A: persisted live position absent in CTP -> externally closed.
        stale: list[str] = []
        for prefix, pos in list(self._active_positions.items()):
            key = (self._strip_exchange(pos.symbol or pos.contract).upper(),
                   pos.direction.lower())
            ctp_match = ctp_index.get(key)
            if pos.liveOpened and ctp_match is None:
                logger.warning(
                    "[LiveEngine] %s 本地持有 %s 但 CTP 不存在 — 视为外部平仓，"
                    "归档本地持仓 (entry=%s @ %s)",
                    prefix, pos.direction, pos.entryTime, pos.entryPrice,
                )
                synthetic_exit = ExitInfo(
                    exitReason="外部平仓",
                    exitTriggerLevel=pos.entryPrice,
                    exitBarClose=pos.entryPrice,
                    exitBarTime=pos.lastEvaluatedBarStamp,
                    exitDate=_now_str(),
                    exitIndex=-1,
                    highestPrice=pos.highestPrice,
                    lowestPrice=pos.lowestPrice,
                    isTrailing=pos.isTrailing,
                )
                self._mark_signal_consumed(prefix, pos.entryBarStamp)
                self._archive_closed_position(
                    prefix, {"lastPrice": pos.entryPrice}, pos, synthetic_exit,
                    exec_result={"status": "external_close",
                                 "reason": "重启时CTP已无对应持仓"},
                )
                stale.append(prefix)
        for prefix in stale:
            self._active_positions.pop(prefix, None)

        # Scenario B: orphan CTP positions not tracked locally — warn only.
        local_keys = {
            (self._strip_exchange(p.symbol or p.contract).upper(),
             p.direction.lower()) for p in self._active_positions.values()
        }
        for key, ctp_pos in ctp_index.items():
            if key not in local_keys:
                logger.warning(
                    "[LiveEngine] CTP 存在未跟踪持仓: %s %s 数量=%s — 不会自动接管",
                    ctp_pos.get("symbol"),
                    ctp_pos.get("direction"),
                    ctp_pos.get("volume"),
                )

    @staticmethod
    def _normalize_position_key(ctp_position: dict) -> tuple[str, str]:
        sym = (ctp_position.get("symbol") or "").split(".")[0].upper()
        direction = (ctp_position.get("direction") or "").lower()
        if direction in ("多", "long", "buy", "净"):
            direction = "long"
        elif direction in ("空", "short", "sell"):
            direction = "short"
        return sym, direction

    @staticmethod
    def _strip_exchange(symbol: str) -> str:
        return (symbol or "").split(".")[0]

    @staticmethod
    def _symbol_exchange(pos: LivePosition) -> str:
        """Extract exchange hint from ``pos.symbol`` (``SR609.CZCE`` → ``CZCE``)."""
        sym = pos.symbol or pos.contract or ""
        if "." in sym:
            return sym.split(".", 1)[1]
        return ""

    # ==================================================================
    # Scan-handling pipeline (per variety)
    # ==================================================================

    def _process_variety(self, row: dict) -> list[dict]:
        prefix = row["prefix"]
        state = self._state_for(prefix, row)
        bars = self._entry_bars(row)
        state["latestBarTime"] = bars[-1]["date"] if bars else None
        transitions: list[dict] = []

        if not bars or len(bars) < 50:
            state["lastError"] = "入场周期数据不足"
            state["lastTransition"] = "insufficient_bars"
            return transitions

        latest_bar_time = bars[-1].get("time", 0)
        if not latest_bar_time:
            state["lastError"] = "最新bar缺少时间戳"
            state["lastTransition"] = "invalid_bar"
            return transitions

        cfg = self._params_for_prefix(prefix)
        prev_open: Optional[LivePosition] = self._active_positions.get(prefix)

        # ──────────────────────────────────────────────────────────────
        # Step 1: independent exit evaluation (the *only* path that can
        # close a live position).
        # ──────────────────────────────────────────────────────────────
        if prev_open and state.get("initialized"):
            atr_period = int(cfg.get("atrPeriod", 14))
            atr_series = self.backtest._calc_atr(bars, atr_period)
            exit_info = prev_open.evaluate_exit(bars, atr_series)
            if exit_info:
                # Mark this historical bsPoint as consumed so the very same
                # bar's snapshot.openPosition can't re-arm itself on the
                # next scan. Without this, a stop-loss + skipped_no_live_open
                # cycle reopens the same order at the same price forever.
                self._mark_signal_consumed(prefix, prev_open.entryBarStamp)
                exec_result = self._archive_closed_position(
                    prefix, row, prev_open, exit_info,
                )
                transitions.append({
                    "varietyCode": prefix,
                    "event": "close",
                    "trade": self._closed_trades[-1] if self._closed_trades else {},
                    "exec": exec_result,
                })
                self._active_positions.pop(prefix, None)
                prev_open = None

        # ──────────────────────────────────────────────────────────────
        # Step 2: same-bar early exit when nothing else can change.
        # ──────────────────────────────────────────────────────────────
        if (state.get("lastProcessedBarStamp") == latest_bar_time
                and state.get("initialized")
                and prev_open is not None):
            state["lastTransition"] = "same_bar_skipped"
            state["lastError"] = ""
            return transitions

        # ──────────────────────────────────────────────────────────────
        # Step 3: snapshot is consulted *only* to spot a new entry signal.
        # When prev_open is alive we don't even need to re-run snapshot.
        # ──────────────────────────────────────────────────────────────
        new_entry: Optional[LivePosition] = None
        if prev_open is None:
            snapshot = self.backtest.snapshot(
                bars,
                cfg,
                {"multiPeriod": row.get("multiPeriod", {})},
            )
            if snapshot.get("error"):
                state["lastError"] = snapshot["error"]
                state["lastTransition"] = "snapshot_error"
                return transitions

            new_entry = self._build_position_from_snapshot(
                prefix, row, snapshot.get("openPosition"), bars, cfg,
            )

        # ──────────────────────────────────────────────────────────────
        # Step 4: bootstrap (first scan post-start, no persisted state).
        # ──────────────────────────────────────────────────────────────
        if not state.get("initialized"):
            state["initialized"] = True
            state["lastProcessedBarStamp"] = latest_bar_time
            state["lastProcessedBarTime"] = bars[-1]["date"]
            state["lastTransition"] = "bootstrap_snapshot"
            state["lastError"] = ""
            if new_entry is not None:
                # If CTP already has the corresponding position (warm restart
                # without persisted state), adopt it as live.
                if self._executor_has_position(new_entry):
                    new_entry.liveOpened = True
                    new_entry.liveStatus = "filled"
                    new_entry.liveReason = "启动时检测到CTP已持有该方向"
                self._active_positions[prefix] = new_entry
                self._record_decision(
                    prefix, row, "bootstrap_snapshot",
                    new_entry.to_display_dict(),
                    {"status": "bootstrap"},
                    reason="启动时同步回测状态快照",
                )
                transitions.append({
                    "varietyCode": prefix,
                    "event": "bootstrap_snapshot",
                    "trade": new_entry.to_display_dict(),
                    "exec": {"status": "bootstrap"},
                })
            return transitions

        # ──────────────────────────────────────────────────────────────
        # Step 5: normal case
        #   - prev_open alive  -> just refresh CTP fill confirmation
        #   - prev_open None + new_entry -> open
        # ──────────────────────────────────────────────────────────────
        if prev_open is not None:
            self._refresh_live_status(prev_open)
            transitions.append({
                "varietyCode": prefix,
                "event": "position_held",
                "trade": prev_open.to_display_dict(),
            })
        elif new_entry is not None:
            exec_result = (self.executor.open_trade(new_entry.to_display_dict())
                           if self.executor else {"status": "disabled"})
            new_entry.liveOpened = self._is_live_filled(exec_result.get("status"))
            new_entry.liveStatus = exec_result.get("status", "disabled")
            new_entry.liveOrderId = exec_result.get("order_id")
            new_entry.liveReason = exec_result.get("reason", "")
            new_entry.liveTradedVolume = exec_result.get("traded_volume")
            self._active_positions[prefix] = new_entry
            self._record_decision(
                prefix, row, "entry_triggered",
                new_entry.to_display_dict(),
                exec_result,
                reason=new_entry.reason,
            )
            transitions.append({
                "varietyCode": prefix,
                "event": "open",
                "trade": new_entry.to_display_dict(),
                "exec": exec_result,
            })
        # else: flat & no new signal -> no-op

        state["lastProcessedBarStamp"] = latest_bar_time
        state["lastProcessedBarTime"] = bars[-1]["date"]
        state["lastTransition"] = transitions[-1]["event"] if transitions else "no_state_change"
        state["lastError"] = ""
        return transitions

    # ==================================================================
    # Helpers — building / decorating / archiving
    # ==================================================================

    def _build_position_from_snapshot(self, prefix: str, row: dict,
                                      snap_pos: dict | None,
                                      bars: list[dict],
                                      cfg: dict) -> Optional[LivePosition]:
        """Convert ``snapshot.openPosition`` into a freshly-frozen LivePosition."""
        if not snap_pos:
            return None

        entry_date = snap_pos.get("entryDate", "") or ""
        entry_bar_stamp = 0
        for bar in bars:
            if bar.get("date") == entry_date:
                entry_bar_stamp = bar.get("time", 0)
                break

        # Hard stop on the reopen-loop bug: if this exact historical bar has
        # already produced a position that was later archived (stopped out /
        # externally closed / skipped), refuse to re-issue the same order.
        # The only way to reopen is for the strategy to fire on a *new* bar.
        if entry_bar_stamp and self._is_signal_consumed(prefix, entry_bar_stamp):
            logger.debug(
                "[LiveEngine] %s 跳过已消费信号 entryBarStamp=%s entry=%s",
                prefix, entry_bar_stamp, entry_date,
            )
            return None

        signal_type = (str(snap_pos.get("strategyType", "")).split(":", 1)[-1]
                       or snap_pos.get("signalFamily", ""))
        align_score = float(snap_pos.get("signalScore") or 0.0)

        return LivePosition(
            varietyCode=prefix,
            symbol=row.get("executionCode", ""),
            contract=row.get("executionCode", ""),
            displayName=row.get("displayName", prefix),
            direction="long" if snap_pos.get("direction") == "long" else "short",
            entryTime=entry_date,
            entryBarStamp=int(entry_bar_stamp or 0),
            entryPrice=float(snap_pos.get("entryPrice") or 0.0),
            initialStopLoss=float(snap_pos.get("stopLoss") or 0.0),
            takeProfit=float(snap_pos.get("takeProfit") or 0.0),
            trailDistance=float(snap_pos.get("trailDistance") or 0.0),
            trailActivation=float(snap_pos.get("trailActivation") or 0.0),
            maxHoldBars=int(snap_pos.get("maxHoldBars")
                            or cfg.get("v14MaxHoldBars", 0) or 0),
            entryAtr=float(snap_pos.get("entryAtr") or 0.0),
            signalType=signal_type,
            signalFamily=snap_pos.get("signalFamily", signal_type),
            strategyType=snap_pos.get("strategyType", ""),
            alignScore=align_score,
            trendContext=snap_pos.get("trendContext", ""),
            reason=snap_pos.get("reason", ""),
            stopLoss=float(snap_pos.get("stopLoss") or 0.0),
            highestPrice=float(snap_pos.get("highestPrice") or 0.0),
            lowestPrice=float(snap_pos.get("lowestPrice") or 0.0),
            isTrailing=bool(snap_pos.get("isTrailing", False)),
            volume=self._trade_volume(),
            priceDecimals=int(cfg.get("priceDecimals", 0) or 0),
        )

    def _archive_closed_position(self, prefix: str, row: dict,
                                 pos: LivePosition,
                                 exit_info: ExitInfo,
                                 exec_result: dict | None = None) -> dict:
        """Build the closed-trade record, route the CTP close, and log it."""
        # Before the archive, cancel any still-active OPEN order for this
        # slot that hasn't filled yet. Without this, a stop-loss archive
        # that marks the signal consumed would leave the original unfilled
        # limit order sitting in CTP's book; if it later gets lifted we'd
        # end up with an "orphan" CTP position the engine refuses to touch.
        if not pos.liveOpened and self.executor is not None:
            try:
                cancelled = getattr(self.executor, "cancel_active_orders",
                                    lambda *a, **kw: [])(
                    symbol=pos.symbol or pos.contract,
                    direction=pos.direction,
                    offset="open",
                    exchange=self._symbol_exchange(pos),
                )
                if cancelled:
                    logger.info(
                        "[LiveEngine] %s 归档前撤销 %d 张未成交开仓委托",
                        prefix, len(cancelled),
                    )
            except Exception as exc:
                logger.warning(
                    "[LiveEngine] %s 归档前撤单失败: %s", prefix, exc,
                )

        if exec_result is None:
            exec_result = self._safe_close_via_executor(pos, exit_info, row)

        order_price = self._resolve_close_order_price(row, exit_info)
        closed = {
            "varietyCode": prefix,
            "displayName": pos.displayName,
            "symbol": pos.symbol,
            "contract": pos.contract,
            "direction": "做多" if pos.direction == "long" else "做空",
            "directionCode": pos.direction,
            "signalType": pos.signalType,
            "signalFamily": pos.signalFamily,
            "strategyType": pos.strategyType,
            "source": "backtest_v14",
            "rule": "",
            "compositeScore": pos.alignScore,
            "v14AlignScore": pos.alignScore,
            "trendContext": pos.trendContext,
            "reason": pos.reason,
            "entryTime": pos.entryTime,
            "entryBarTime": pos.entryTime,
            "entryPrice": pos.entryPrice,
            "stopLoss": pos.stopLoss,
            "takeProfit": pos.takeProfit,
            "trailDistance": pos.trailDistance,
            "trailActivation": pos.trailActivation,
            "highestPrice": exit_info.highestPrice,
            "lowestPrice": exit_info.lowestPrice,
            "isTrailing": exit_info.isTrailing,
            "exitTime": exit_info.exitDate or _now_str(),
            "exitPrice": order_price,
            "exitTriggerLevel": exit_info.exitTriggerLevel,
            "exitReason": exit_info.exitReason,
            "status": exit_info.exitReason or "已平仓",
            "stateKey": f"{pos.state_key()}|{exit_info.exitDate}|{exit_info.exitReason}",
            "entryKey": pos.state_key(),
            "volume": pos.volume,
            "liveOpened": pos.liveOpened,
            "liveStatus": pos.liveStatus,
            "liveOrderId": pos.liveOrderId,
            "liveReason": pos.liveReason,
            "liveCloseStatus": exec_result.get("status", "disabled"),
            "liveCloseOrderId": exec_result.get("order_id"),
            "liveCloseReason": exec_result.get("reason", ""),
            "holdMinutes": _minutes_between(pos.entryTime,
                                            exit_info.exitDate or _now_str()),
            "pnl": None,
        }
        closed = self._with_net_pnl(prefix, closed)

        self._closed_trades.append(closed)
        if len(self._closed_trades) > _MAX_CLOSED_TRADES:
            self._closed_trades[:] = self._closed_trades[-_MAX_CLOSED_TRADES:]

        self._record_decision(
            prefix, row, self._exit_event_name(exit_info.exitReason),
            closed, exec_result, reason=exit_info.exitReason,
        )
        logger.info(
            "[LiveEngine] %s 平仓触发: %s @ %s (触发位=%s) entry=%s 已成交=%s 状态=%s",
            prefix, exit_info.exitReason, order_price,
            exit_info.exitTriggerLevel, pos.entryTime,
            exec_result.get("traded_volume"), exec_result.get("status"),
        )
        return exec_result

    def _safe_close_via_executor(self, pos: LivePosition,
                                 exit_info: ExitInfo, row: dict) -> dict:
        if not self.executor:
            return {"status": "disabled", "reason": "无执行器"}
        if (not pos.liveOpened
                and not self._executor_has_position(pos)
                and pos.liveStatus != "queued"):
            # Truly nothing to close. When status is "queued" we still
            # forward to close_trade so it can drop the still-pending
            # conditional open from the queue.
            return {
                "status": "skipped_no_live_open",
                "reason": "上一笔未实盘开仓，无需平仓",
            }
        order_price = self._resolve_close_order_price(row, exit_info)
        order_payload = pos.to_display_dict()
        order_payload.update({
            "exitPrice": order_price,
            "exitReason": exit_info.exitReason,
            "exitTriggerLevel": exit_info.exitTriggerLevel,
        })
        try:
            return self.executor.close_trade(order_payload, order_payload)
        except Exception as exc:
            logger.exception("[LiveEngine] %s 调用 executor.close_trade 失败: %s",
                             pos.varietyCode, exc)
            return {"status": "failed", "reason": str(exc)}

    @staticmethod
    def _resolve_close_order_price(row: dict, exit_info: ExitInfo) -> float:
        """Pick a price aggressive enough to fill while still meaningful."""
        last_price = _safe_float(row.get("lastPrice"))
        if last_price > 0:
            return last_price
        if exit_info.exitBarClose > 0:
            return exit_info.exitBarClose
        return exit_info.exitTriggerLevel

    def _refresh_live_status(self, pos: LivePosition) -> None:
        """If the original open order was pending/queued, check CTP fill."""
        if pos.liveOpened:
            return
        if pos.liveStatus not in ("submitted", "pending", "part_traded", "queued"):
            return
        if self._executor_has_position(pos):
            pos.liveOpened = True
            pos.liveStatus = "filled"
            pos.liveReason = "扫描期间检测到CTP已持有该方向"

    # ==================================================================
    # Pure helpers
    # ==================================================================

    def _state_for(self, prefix: str, row: dict) -> dict:
        state = self._variety_state.get(prefix)
        if state is None:
            state = self._blank_variety_state(prefix, row.get("displayName", prefix))
            self._variety_state[prefix] = state
        else:
            state["displayName"] = row.get("displayName", prefix)
            state["entryTimeframe"] = self.config.strategy.entry_timeframe
        return state

    def _blank_variety_state(self, prefix: str, display_name: str) -> dict:
        return {
            "displayName": display_name,
            "entryTimeframe": self.config.strategy.entry_timeframe,
            "initialized": False,
            "lastProcessedBarStamp": 0,
            "lastProcessedBarTime": None,
            "latestBarTime": None,
            "lastTransition": "",
            "lastError": "",
        }

    def _entry_bars(self, row: dict) -> list[dict]:
        timeframe = self.config.strategy.entry_timeframe
        bars = (row.get("timeframeBars") or {}).get(timeframe) or []
        if bars:
            return bars
        if timeframe != "15m":
            return (row.get("timeframeBars") or {}).get("15m") or []
        return bars

    def _params_for_prefix(self, prefix: str) -> dict:
        spec = get_spec_by_prefix(prefix) or {}
        return {
            "contractMultiplier": spec.get("multiplier", 10),
            "commissionPerLot": spec.get("commission", 1.21),
            "v14StopATR": self.config.strategy.stop_atr,
            "v14TargetATR": self.config.strategy.target_atr,
            "v14TrailATR": self.config.strategy.trail_atr,
            "v14MaxHoldBars": self.config.strategy.max_hold_bars,
            "v14Cooldown": self.config.strategy.cooldown,
            "v14MinAlignScore": self.config.strategy.min_align_score,
            "v14Preset": self.config.strategy.preset,
            "v14EntryTimeframe": self.config.strategy.entry_timeframe,
        }

    def _trade_volume(self) -> int:
        auto_trade = getattr(self.config, "auto_trade", None)
        return max(1, int(getattr(auto_trade, "volume_per_signal", 1) or 1))

    def _with_net_pnl(self, prefix: str, trade: dict) -> dict:
        entry = _safe_float(trade.get("entryPrice"))
        exit_price = _safe_float(trade.get("exitPrice"))
        if entry <= 0 or exit_price <= 0:
            return trade

        spec = get_spec_by_prefix(prefix) or {}
        multiplier = _safe_float(spec.get("multiplier")) or 10.0
        commission_per_lot = _safe_float(spec.get("commission"))
        volume = max(1, int(_safe_float(trade.get("volume")) or self._trade_volume()))
        direction = trade.get("directionCode") or trade.get("direction", "")
        is_long = direction in ("long", "做多")
        diff = exit_price - entry if is_long else entry - exit_price
        gross = diff * multiplier * volume
        commission = commission_per_lot * 2 * volume
        trade["volume"] = volume
        trade["contractMultiplier"] = multiplier
        trade["commission"] = round(commission, 2)
        trade["grossPnl"] = round(gross, 2)
        trade["pnl"] = round(gross - commission, 2)
        return trade

    @staticmethod
    def _is_live_filled(status) -> bool:
        return status in ("filled", "part_traded", "position_confirmed")

    def _executor_has_position(self, pos_or_trade) -> bool:
        ctp = getattr(self.executor, "ctp", None) if self.executor else None
        if not ctp or not hasattr(ctp, "has_position"):
            return False
        if isinstance(pos_or_trade, LivePosition):
            symbol_with_ex = pos_or_trade.symbol or pos_or_trade.contract
            direction = pos_or_trade.direction
        else:
            symbol_with_ex = pos_or_trade.get("symbol") or pos_or_trade.get("contract", "")
            direction = pos_or_trade.get("directionCode") or pos_or_trade.get("direction", "")
            direction = "long" if direction in ("long", "做多") else "short"
        if not symbol_with_ex:
            return False
        symbol = symbol_with_ex.split(".")[0]
        exchange = symbol_with_ex.split(".")[-1] if "." in symbol_with_ex else ""
        try:
            return bool(ctp.has_position(symbol, direction, exchange))
        except Exception:
            return False

    @staticmethod
    def _open_status_label(pos: LivePosition) -> str:
        if pos.liveOpened:
            return "持仓中"
        live_status = pos.liveStatus or "tracking"
        return {
            "rejected": "开仓被拒",
            "failed": "开仓失败",
            "cancelled": "委托已撤销",
            "cancelled_queued": "条件单已撤销",
            "disabled": "策略跟踪(未开启)",
            "ctp_not_ready": "策略跟踪(CTP未就绪)",
            "submitted": "委托已提交",
            "pending": "委托已提交",
            "queued": "条件单待发(等待开盘)",
            "part_traded": "部分成交",
            "filled": "持仓中",
            "position_confirmed": "持仓中",
        }.get(live_status, "策略持仓")

    @staticmethod
    def _exit_event_name(exit_reason: str) -> str:
        if EXIT_STOP_LOSS in exit_reason:
            return "exit_stop_loss"
        if EXIT_TAKE_PROFIT in exit_reason:
            return "exit_take_profit"
        if EXIT_TRAILING in exit_reason:
            return "exit_trailing"
        if EXIT_TIMEOUT in exit_reason:
            return "exit_timeout"
        if "外部" in exit_reason:
            return "exit_external"
        return "exit_close"

    def _record_decision(self, prefix: str, row: dict, event: str,
                         trade: dict, exec_result: dict, reason: str) -> None:
        exec_reason = exec_result.get("reason", "") if exec_result else ""
        live_status = exec_result.get("status", "tracking") if exec_result else "tracking"
        combined_reason = reason or ""
        if exec_reason and live_status in (
            "rejected", "failed", "skipped", "skipped_no_live_open",
            "ctp_not_ready", "disabled", "cancelled", "external_close",
        ):
            combined_reason = (f"{combined_reason} | {exec_reason}"
                               if combined_reason else exec_reason)
        entry = {
            "time": _now_str(),
            "varietyCode": prefix,
            "displayName": row.get("displayName", prefix),
            "direction": trade.get("directionCode", ""),
            "directionLabel": trade.get("direction", ""),
            "signalType": trade.get("signalType", ""),
            "source": "backtest_v14",
            "event": event,
            "liveStatus": live_status,
            "reason": combined_reason,
            "orderId": exec_result.get("order_id") if exec_result else None,
            "barTime": _state_bar_time(row, self.config.strategy.entry_timeframe),
        }
        self._decision_log.append(entry)
        if len(self._decision_log) > _MAX_DECISION_LOG:
            self._decision_log = self._decision_log[-_MAX_DECISION_LOG:]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _state_bar_time(row: dict, timeframe: str) -> str:
    bars = (row.get("timeframeBars") or {}).get(timeframe) or []
    if not bars and timeframe != "15m":
        bars = (row.get("timeframeBars") or {}).get("15m") or []
    if not bars:
        return ""
    return bars[-1].get("date", "")


# Kept around for backwards-compatibility with any external callers that
# previously imported the helper from this module.
state_bar_time = _state_bar_time


def _minutes_since(time_str: str) -> int:
    if not time_str:
        return 0
    try:
        entry = datetime.fromisoformat(time_str)
        return int((datetime.now() - entry).total_seconds() / 60)
    except Exception:
        return 0


def _minutes_between(start: str, end: str) -> int:
    if not start or not end:
        return 0
    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        return int((end_dt - start_dt).total_seconds() / 60)
    except Exception:
        return 0


def _safe_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0
