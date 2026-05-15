"""CTP execution adapter for the live trading engine.

This layer replaces the former "paper/模拟记录" path. Order decisions are
always forwarded to the real CTP gateway when auto-trading is enabled and
CTP is ready. No silent fallback to simulated records — callers get an
explicit status indicating why a decision could not be executed.

Status values returned by open_trade/close_trade:
    filled               委托已全部成交，CTP确认持仓已建立 (开仓) / 已平仓
    part_traded          委托部分成交
    submitted            委托已发送，但等待时间内未拿到成交回报 (后续仍可能成交)
    queued               非交易时段，已写入条件单队列，下一个时段开盘自动派发
    duplicate_active     同合约同方向同开平已有未成交委托，本轮跳过 (占用保证金、
                           再次下单会触发 "资金不足")
    rejected             风控拒绝 / CTP拒单
    cancelled            订单被撤销
    cancelled_queued     撤销了尚未派发的条件单 (由于随后的反向平仓请求)
    skipped              无需执行 (例如平仓时CTP未持有对应方向)
    skipped_no_live_open 上一笔从未真正开仓，跳过实盘平仓
    disabled             auto_trade 开关关闭
    ctp_not_ready        CTP 未连接或未就绪
    failed               下单过程中出现异常
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from .pending_orders import PendingOrderQueue
from .trading_hours import is_trading_now, next_session_open

logger = logging.getLogger(__name__)

_MAX_ORDER_LOG = 500
_DEFAULT_PENDING_PATH = (Path(__file__).resolve().parent.parent
                         / "data" / "pending_orders.json")


class AutoOrderManager:
    """Execute already-decided open/close actions on CTP.

    Parameters
    ----------
    ctp_client:
        Live CTP client wrapper (``finme_quant.trading.ctp_client.CTPClient``).
    config:
        Dict-like (or pydantic-dumped) auto_trade configuration.
    persist_fn:
        Optional callable invoked as ``persist_fn(enabled: bool)`` when
        :meth:`set_enabled` is called. Used to persist the new value to
        ``config.yaml`` so the UI toggle survives restarts.
    """

    def __init__(
        self,
        ctp_client=None,
        config: dict | None = None,
        *,
        persist_fn: Callable[[bool], None] | None = None,
        pending_path: str | Path | None = None,
    ) -> None:
        self.ctp = ctp_client
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.max_position_per_variety = int(cfg.get("max_position_per_variety", 1))
        self.max_total_positions = int(cfg.get("max_total_positions", 3))
        self.volume_per_signal = int(cfg.get("volume_per_signal", 1))
        self.order_wait_seconds = float(cfg.get("order_wait_seconds", 3.0))
        self.min_score = float(cfg.get("min_score", 30))
        # When True, signals produced outside the CTP trading session are
        # persisted into a local queue and dispatched automatically by
        # ``flush_pending()`` once the market re-opens. Keeps the CTP
        # gateway from spamming "委托请求发送失败，错误代码：-1" on every
        # scan during lunch / overnight breaks.
        self.queue_when_closed = bool(cfg.get("queue_when_closed", True))
        self._persist_fn = persist_fn
        self.order_log: list[dict] = []
        self.pending_orders = PendingOrderQueue(
            pending_path or _DEFAULT_PENDING_PATH,
            ttl_hours=float(cfg.get("pending_ttl_hours", 18)),
        )

    # ------------------------------------------------------------------
    # Runtime toggle
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> bool:
        """Enable/disable auto-trading at runtime; persist if configured."""
        new_val = bool(enabled)
        if new_val != self.enabled:
            self.enabled = new_val
            logger.info("[AutoOrder] 自动下单 %s", "已启用" if new_val else "已禁用")
        if self._persist_fn:
            try:
                self._persist_fn(new_val)
            except Exception as exc:
                logger.error("[AutoOrder] 持久化 auto_trade.enabled 失败: %s", exc)
        return self.enabled

    # ------------------------------------------------------------------
    # Trading actions
    # ------------------------------------------------------------------

    def open_trade(self, trade: dict) -> dict:
        """Submit a real open order if auto-trading is on and CTP is ready.

        Waits up to ``order_wait_seconds`` for CTP to confirm a fill so the
        caller can reliably determine whether the position is actually live.
        """
        if not self.enabled:
            return {"status": "disabled", "reason": "自动下单未开启"}
        if not self.ctp or not self.ctp.ready:
            return {"status": "ctp_not_ready", "reason": "CTP未就绪"}

        symbol, exchange = self._parse_symbol(trade.get("symbol") or trade.get("contract", ""))
        if not symbol:
            return {"status": "failed", "reason": "缺少可下单合约"}
        order_symbol = self._resolve_contract_symbol(symbol, exchange)

        ok, reason = self._check_risk(trade)
        if not ok:
            logger.warning("[AutoOrder] 风控拒绝开仓: %s — %s", order_symbol, reason)
            return {"status": "rejected", "reason": reason}

        direction = trade.get("directionCode") or trade.get("direction", "")
        direction = "long" if direction in ("long", "做多") else "short"
        price = float(trade.get("entryPrice") or 0)
        if price <= 0:
            return {"status": "failed", "reason": "缺少有效的入场价"}

        dup = self._active_duplicate(order_symbol, direction, "open", exchange)
        if dup is not None:
            logger.info(
                "[AutoOrder] %s %s 已有未成交开仓委托 (id=%s 价=%s 量=%s)，跳过本次开仓",
                order_symbol, direction,
                dup.get("vt_orderid") or dup.get("order_id"),
                dup.get("price"), dup.get("volume"),
            )
            return {
                "status": "duplicate_active",
                "order_id": dup.get("vt_orderid") or dup.get("order_id"),
                "symbol": order_symbol,
                "exchange": exchange,
                "traded_volume": float(dup.get("traded") or 0),
                "total_volume": float(dup.get("volume") or 0),
                "reason": (f"同合约同方向已有未成交开仓委托 "
                           f"(id={dup.get('vt_orderid') or dup.get('order_id')}, "
                           f"价={dup.get('price')})，避免重复锁定保证金"),
            }

        variety = str(trade.get("varietyCode") or "").upper()
        # If the market for this product is closed, avoid spamming CTP with
        # orders that will be rejected with error code -1. Persist the
        # intent as a conditional order and dispatch it at the next open.
        if self.queue_when_closed and variety and not is_trading_now(variety):
            queued = self.pending_orders.enqueue({
                "action": "open",
                "variety_code": variety,
                "symbol": order_symbol,
                "exchange": exchange,
                "direction": direction,
                "offset": "open",
                "price": price,
                "volume": self.volume_per_signal,
                "reason": trade.get("reason", ""),
                "signal_type": trade.get("signalType", ""),
                "state_key": trade.get("stateKey", ""),
            })
            nxt = next_session_open(variety).strftime("%Y-%m-%d %H:%M")
            logger.info(
                "[AutoOrder] %s 当前处于非交易时段，已加入条件单队列 (下一开盘=%s)",
                order_symbol, nxt,
            )
            return {
                "status": "queued",
                "order_id": "",
                "symbol": order_symbol,
                "exchange": exchange,
                "traded_volume": 0.0,
                "total_volume": float(self.volume_per_signal),
                "reason": f"非交易时段，已挂条件单；下一开盘={nxt}",
                "queued_at": queued.get("queued_at"),
            }

        try:
            result = self.ctp.place_order(
                symbol=f"{order_symbol}.{exchange}" if exchange else order_symbol,
                direction=direction,
                offset="open",
                price=price,
                volume=self.volume_per_signal,
                exchange=exchange,
                wait_seconds=self.order_wait_seconds,
            )
        except Exception as exc:
            logger.error("[AutoOrder] CTP开仓失败: %s %s — %s", order_symbol, direction, exc)
            return {"status": "failed", "reason": str(exc)}

        status = result.get("status", "submitted")
        traded = float(result.get("traded_volume") or 0)
        total = float(result.get("total_volume") or self.volume_per_signal)
        # 二次校验：哪怕委托回报还没到，只要CTP的持仓已经包含我们的方向且≥成交数量，也判定开仓生效
        if status not in ("filled",) and self._verify_position(order_symbol, direction, exchange):
            status = "filled"
            if traded <= 0:
                traded = total

        reject_reason = result.get("reject_reason") or ""
        record = {
            "symbol": result.get("symbol") or order_symbol,
            "exchange": result.get("exchange") or exchange,
            "direction": direction,
            "offset": "open",
            "price": price,
            "volume": int(total) if total > 0 else self.volume_per_signal,
            "traded_volume": traded,
            "datetime": datetime.now().isoformat(timespec="seconds"),
            "order_id": result.get("order_id"),
            "status": status,
            "reason": reject_reason or self._status_reason(status),
        }
        self._append_log(record)
        logger.info(
            "[AutoOrder] CTP开仓: %s %s @ %s x%d id=%s 状态=%s 已成交=%s%s",
            direction, record["symbol"], price, self.volume_per_signal,
            result.get("order_id"), status, traded,
            f" 原因={reject_reason}" if reject_reason else "",
        )
        return record

    def close_trade(self, open_trade: dict, closed_trade: dict) -> dict:
        """Submit a real close order based on actual CTP holdings.

        - 若上一笔从未实盘开仓 (``liveOpened`` 为 False) 且 CTP 也没有相应持仓，
          直接返回 ``skipped_no_live_open``，不向 CTP 发任何委托。
        - 否则按 CTP 真实持有的数量发起平仓，并等待终态。
        """
        if not self.enabled:
            return {"status": "disabled", "reason": "自动下单未开启"}
        if not self.ctp or not self.ctp.ready:
            return {"status": "ctp_not_ready", "reason": "CTP未就绪"}

        symbol, exchange = self._parse_symbol(open_trade.get("symbol") or open_trade.get("contract", ""))
        if not symbol:
            return {"status": "failed", "reason": "缺少可平仓合约"}
        order_symbol = self._resolve_contract_symbol(symbol, exchange)

        direction = open_trade.get("directionCode") or open_trade.get("direction", "")
        direction = "long" if direction in ("long", "做多") else "short"
        close_volume = self._get_closeable_volume(order_symbol, direction)
        prev_live = bool(open_trade.get("liveOpened", False))

        # If we still had a *queued* open order for this slot (dispatched
        # yet? no), drop it so the matching close doesn't dispatch first.
        cancelled_queued_open = self._drop_queued_open(order_symbol, direction)

        if close_volume <= 0:
            if cancelled_queued_open:
                return {
                    "status": "cancelled_queued",
                    "reason": "撤销了尚未派发的条件开仓单",
                }
            if not prev_live:
                logger.info(
                    "[AutoOrder] 跳过平仓: %s %s 之前未实盘开仓 (liveOpened=False) 且CTP也无持仓",
                    order_symbol, direction,
                )
                return {
                    "status": "skipped_no_live_open",
                    "reason": "上一笔未实盘开仓，无需平仓",
                }
            logger.warning(
                "[AutoOrder] 状态不一致: %s %s 策略标记为liveOpened，但CTP未持有对应方向 — 跳过平仓",
                order_symbol, direction,
            )
            return {"status": "skipped", "reason": "CTP无对应方向持仓"}

        exit_price = float(closed_trade.get("exitPrice") or closed_trade.get("entryPrice") or 0)
        if exit_price <= 0:
            return {"status": "failed", "reason": "缺少有效的平仓价"}

        close_dir = "short" if direction == "long" else "long"

        dup = self._active_duplicate(order_symbol, close_dir, "close", exchange)
        if dup is not None:
            logger.info(
                "[AutoOrder] %s %s 已有未成交平仓委托 (id=%s 价=%s)，跳过本次平仓",
                order_symbol, close_dir,
                dup.get("vt_orderid") or dup.get("order_id"),
                dup.get("price"),
            )
            return {
                "status": "duplicate_active",
                "order_id": dup.get("vt_orderid") or dup.get("order_id"),
                "symbol": order_symbol,
                "exchange": exchange,
                "traded_volume": float(dup.get("traded") or 0),
                "total_volume": float(dup.get("volume") or 0),
                "reason": (f"同合约同方向已有未成交平仓委托 "
                           f"(id={dup.get('vt_orderid') or dup.get('order_id')}, "
                           f"价={dup.get('price')})，等待前单终态"),
            }

        variety = str(open_trade.get("varietyCode") or "").upper()
        if self.queue_when_closed and variety and not is_trading_now(variety):
            queued = self.pending_orders.enqueue({
                "action": "close",
                "variety_code": variety,
                "symbol": order_symbol,
                "exchange": exchange,
                "direction": close_dir,
                "offset": "close",
                "price": exit_price,
                "volume": close_volume,
                "reason": closed_trade.get("exitReason", ""),
                "signal_type": open_trade.get("signalType", ""),
                "state_key": closed_trade.get("stateKey", ""),
            })
            nxt = next_session_open(variety).strftime("%Y-%m-%d %H:%M")
            logger.info(
                "[AutoOrder] %s 当前处于非交易时段，平仓已加入条件单队列 (下一开盘=%s)",
                order_symbol, nxt,
            )
            return {
                "status": "queued",
                "order_id": "",
                "symbol": order_symbol,
                "exchange": exchange,
                "traded_volume": 0.0,
                "total_volume": float(close_volume),
                "reason": f"非交易时段，平仓已挂条件单；下一开盘={nxt}",
                "queued_at": queued.get("queued_at"),
            }

        try:
            result = self.ctp.place_order(
                symbol=f"{order_symbol}.{exchange}" if exchange else order_symbol,
                direction=close_dir,
                offset="close",
                price=exit_price,
                volume=close_volume,
                exchange=exchange,
                wait_seconds=self.order_wait_seconds,
            )
        except Exception as exc:
            logger.error("[AutoOrder] CTP平仓失败: %s %s — %s", order_symbol, direction, exc)
            return {"status": "failed", "reason": str(exc)}

        status = result.get("status", "submitted")
        traded = float(result.get("traded_volume") or 0)
        total = float(result.get("total_volume") or close_volume)
        # 二次校验：CTP 该方向持仓清零或低于原持仓 -> 视为已平仓
        if status not in ("filled",):
            remaining = self._get_closeable_volume(order_symbol, direction)
            if remaining <= 0:
                status = "filled"
                if traded <= 0:
                    traded = total

        reject_reason = result.get("reject_reason") or ""
        record = {
            "symbol": result.get("symbol") or order_symbol,
            "exchange": result.get("exchange") or exchange,
            "direction": close_dir,
            "offset": "close",
            "price": exit_price,
            "volume": int(total) if total > 0 else close_volume,
            "traded_volume": traded,
            "datetime": datetime.now().isoformat(timespec="seconds"),
            "order_id": result.get("order_id"),
            "status": status,
            "reason": reject_reason or self._status_reason(status),
        }
        self._append_log(record)
        logger.info(
            "[AutoOrder] CTP平仓: %s %s @ %s x%d id=%s 状态=%s 已成交=%s%s",
            direction, record["symbol"], exit_price, close_volume,
            result.get("order_id"), status, traded,
            f" 原因={reject_reason}" if reject_reason else "",
        )
        return record

    # ------------------------------------------------------------------
    # Pending / conditional orders
    # ------------------------------------------------------------------

    def flush_pending(self) -> list[dict]:
        """Dispatch all queued orders whose market is now open.

        Meant to be called once per scan loop iteration. Returns the list
        of dispatch results (mostly useful for logging).
        """
        if not self.enabled:
            return []
        if not self.ctp or not self.ctp.ready:
            return []
        if len(self.pending_orders) == 0:
            return []

        return self.pending_orders.flush(
            is_tradeable=lambda prefix: is_trading_now(prefix),
            dispatch=self._dispatch_queued,
        )

    def _dispatch_queued(self, item: dict) -> dict:
        """Send a previously-queued order via CTP."""
        action = (item.get("action") or "").lower()
        order_symbol = str(item.get("symbol") or "")
        exchange = str(item.get("exchange") or "")
        direction = str(item.get("direction") or "").lower()
        price = float(item.get("price") or 0)
        volume = int(item.get("volume") or 0)

        if not order_symbol or price <= 0 or volume <= 0:
            return {"status": "failed", "reason": "条件单字段缺失"}

        offset = "close" if action == "close" else "open"
        # For close orders, re-query CTP so we don't close more than we
        # actually hold (e.g. user manually reduced the position during
        # the overnight gap).
        if offset == "close":
            held_direction = "short" if direction == "long" else "long"
            current = self._get_closeable_volume(order_symbol, held_direction)
            if current <= 0:
                logger.info(
                    "[AutoOrder] 条件平仓取消: %s %s CTP已无该方向持仓",
                    order_symbol, held_direction,
                )
                return {"status": "skipped", "reason": "CTP无对应方向持仓"}
            if current < volume:
                logger.warning(
                    "[AutoOrder] 条件平仓调整数量: %s %s 队列=%d -> 实际=%d",
                    order_symbol, held_direction, volume, current,
                )
                volume = current

        # Dedup: if the open/close slot still has a live (unfilled) order,
        # don't fire another one — that's exactly how we previously burned
        # margin and hit "资金不足".
        dup = self._active_duplicate(order_symbol, direction, offset, exchange)
        if dup is not None:
            logger.info(
                "[AutoOrder] 条件单跳过 %s %s %s @ %s: 已有未成交委托 id=%s 价=%s",
                offset, direction, order_symbol, price,
                dup.get("vt_orderid") or dup.get("order_id"), dup.get("price"),
            )
            return {
                "status": "duplicate_active",
                "order_id": dup.get("vt_orderid") or dup.get("order_id"),
                "symbol": order_symbol,
                "exchange": exchange,
                "reason": "同合约同方向已有未成交委托，待前单终态后再派发",
                "traded_volume": float(dup.get("traded") or 0),
                "total_volume": float(dup.get("volume") or 0),
            }
        try:
            result = self.ctp.place_order(
                symbol=f"{order_symbol}.{exchange}" if exchange else order_symbol,
                direction=direction,
                offset=offset,
                price=price,
                volume=volume,
                exchange=exchange,
                wait_seconds=self.order_wait_seconds,
            )
        except Exception as exc:
            logger.error("[AutoOrder] 条件单派发失败: %s %s — %s",
                         order_symbol, direction, exc)
            return {"status": "failed", "reason": str(exc)}

        status = result.get("status", "submitted")
        traded = float(result.get("traded_volume") or 0)
        total = float(result.get("total_volume") or volume)
        reject_reason = result.get("reject_reason") or ""

        record = {
            "symbol": result.get("symbol") or order_symbol,
            "exchange": result.get("exchange") or exchange,
            "direction": direction,
            "offset": offset,
            "price": price,
            "volume": int(total) if total > 0 else volume,
            "traded_volume": traded,
            "datetime": datetime.now().isoformat(timespec="seconds"),
            "order_id": result.get("order_id"),
            "status": status,
            "reason": reject_reason or self._status_reason(status),
            "from_queue": True,
            "queued_at": item.get("queued_at"),
        }
        self._append_log(record)
        logger.info(
            "[AutoOrder] 条件单派发: %s %s %s @ %s x%d id=%s 状态=%s 已成交=%s%s",
            offset, direction, record["symbol"], price, volume,
            result.get("order_id"), status, traded,
            f" 原因={reject_reason}" if reject_reason else "",
        )
        return record

    def _drop_queued_open(self, order_symbol: str, direction: str) -> bool:
        """Remove a still-queued open order for the given slot, if any."""
        order_symbol = (order_symbol or "").upper()
        direction = (direction or "").lower()
        match = None
        for item in self.pending_orders.items:
            if ((item.get("symbol") or "").upper() == order_symbol
                    and (item.get("direction") or "").lower() == direction
                    and (item.get("offset") or "").lower() == "open"):
                match = item
                break
        if match is None:
            return False
        self.pending_orders.remove(match)
        logger.info(
            "[AutoOrder] 取消尚未派发的条件开仓单: %s %s (策略已要求平仓)",
            order_symbol, direction,
        )
        return True

    def cancel_active_orders(self, symbol: str, direction: str = "",
                             offset: str = "", exchange: str = "") -> list[dict]:
        """Cancel every still-active CTP order for the given slot.

        Called by the engine when it archives a virtual position whose
        original order never filled — otherwise the orphan order could
        quietly fill later and leave us with a CTP position the strategy
        believes has been consumed.
        """
        if not self.ctp or not hasattr(self.ctp, "get_active_orders"):
            return []
        try:
            actives = self.ctp.get_active_orders(
                symbol=symbol, direction=direction, offset=offset,
                exchange_hint=exchange,
            ) or []
        except Exception as exc:
            logger.warning("[AutoOrder] 查询活跃委托失败 (%s)", exc)
            return []

        cancelled: list[dict] = []
        for rec in actives:
            oid = rec.get("vt_orderid") or rec.get("order_id")
            if not oid:
                continue
            try:
                self.ctp.cancel_order(oid)
            except Exception as exc:
                logger.warning("[AutoOrder] 撤单失败 id=%s — %s", oid, exc)
                continue
            logger.info(
                "[AutoOrder] 已提交撤单: id=%s %s %s %s @ %s (%s未成交)",
                oid, rec.get("direction"), rec.get("offset"),
                rec.get("symbol"), rec.get("price"),
                rec.get("volume"),
            )
            cancelled.append(rec)
        return cancelled

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _append_log(self, record: dict) -> None:
        self.order_log.append(record)
        if len(self.order_log) > _MAX_ORDER_LOG:
            self.order_log[:] = self.order_log[-_MAX_ORDER_LOG:]

    @staticmethod
    def _parse_symbol(exchange_code: str) -> tuple[str, str]:
        if not exchange_code:
            return "", ""
        parts = str(exchange_code).split(".")
        symbol = parts[0]
        exchange = AutoOrderManager._normalize_exchange(parts[-1]) if len(parts) > 1 else ""
        return symbol, exchange

    @staticmethod
    def _normalize_exchange(exchange: str) -> str:
        aliases = {"ZCE": "CZCE"}
        key = str(exchange or "").upper()
        return aliases.get(key, key)

    def _resolve_contract_symbol(self, symbol: str, exchange: str = "") -> str:
        if self.ctp and hasattr(self.ctp, "resolve_contract_symbol"):
            try:
                return self.ctp.resolve_contract_symbol(symbol, exchange)
            except Exception:
                pass
        return symbol

    def _verify_position(self, symbol: str, direction: str, exchange: str = "") -> bool:
        """Query CTP positions to confirm the given direction is held."""
        if not self.ctp or not hasattr(self.ctp, "has_position"):
            return False
        try:
            return bool(self.ctp.has_position(symbol, direction, exchange))
        except Exception:
            return False

    def _active_duplicate(self, symbol: str, direction: str, offset: str,
                          exchange: str = "") -> dict | None:
        """Return the most recent unfilled order for this (symbol, dir, offset) slot.

        CTP keeps margin locked on every open limit order. If we send a
        second identical request while the first is still active, the
        account's *available* balance has already been shrunk by the
        first one — which is why the log reports ``CTP:资金不足``. We
        therefore skip the new request and wait for the first one's
        terminal status.
        """
        if not self.ctp or not hasattr(self.ctp, "get_active_orders"):
            return None
        try:
            actives = self.ctp.get_active_orders(
                symbol=symbol, direction=direction, offset=offset,
                exchange_hint=exchange,
            ) or []
        except Exception as exc:
            logger.debug("[AutoOrder] get_active_orders 失败 (%s) — 不拦截重复单", exc)
            return None
        if not actives:
            return None
        # Newest first so the log surfaces the most recent id.
        actives.sort(key=lambda r: r.get("datetime", ""), reverse=True)
        return actives[0]

    @staticmethod
    def _status_reason(status: str) -> str:
        return {
            "filled": "全部成交",
            "part_traded": "部分成交",
            "submitted": "委托已提交",
            "pending": "等待成交",
            "queued": "非交易时段，等待开盘派发",
            "cancelled_queued": "撤销了尚未派发的条件单",
            "duplicate_active": "已有未成交委托，避免重复锁定保证金",
            "rejected": "委托被拒",
            "cancelled": "已撤销",
            "skipped": "无需执行",
        }.get(status, "")

    def _check_risk(self, trade: dict) -> tuple[bool, str]:
        score = self._signal_score(trade)
        if self.min_score > 0 and score < self.min_score:
            return (False,
                    f"评分 {score:g} 低于阈值 {self.min_score:g}，不进入实盘交易")
        try:
            positions = self.ctp.get_positions()
        except Exception as exc:
            return False, f"无法读取CTP持仓: {exc}"
        if len(positions) >= self.max_total_positions:
            return False, f"总持仓已达上限 ({len(positions)}/{self.max_total_positions})"
        variety = trade.get("varietyCode", "")
        if variety:
            variety_positions = [
                pos for pos in positions
                if (pos.get("symbol") or "").upper().startswith(variety.upper())
            ]
            if len(variety_positions) >= self.max_position_per_variety:
                return (False,
                        f"{variety} 同品种持仓已达上限 "
                        f"({len(variety_positions)}/{self.max_position_per_variety})")
        return True, ""

    @staticmethod
    def _signal_score(trade: dict) -> float:
        for key in ("compositeScore", "signalScore", "v14AlignScore"):
            v = trade.get(key)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _get_closeable_volume(self, symbol: str, direction: str) -> int:
        try:
            positions = self.ctp.get_positions()
        except Exception:
            return 0

        sym_upper = symbol.upper()
        for pos in positions:
            p_sym = (pos.get("symbol") or "").upper()
            p_dir = (pos.get("direction") or "").lower()
            p_vol = int(pos.get("volume") or 0)
            if not (p_sym == sym_upper or p_sym.startswith(sym_upper + ".")):
                continue
            dir_match = (
                (direction == "long" and p_dir in ("long", "多", "净"))
                or (direction == "short" and p_dir in ("short", "空", "净"))
            )
            if dir_match and p_vol > 0:
                return p_vol
        return 0
