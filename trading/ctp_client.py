"""CTP trading client using vnpy directly (no Flask HTTP layer).

Refactored from ctp_trading_server.py to be used in-process.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from vnpy.event import Event, EventEngine
    from vnpy.trader.constant import Direction, Exchange, Offset, OrderType
    from vnpy.trader.engine import MainEngine
    from vnpy.trader.event import (
        EVENT_ACCOUNT, EVENT_CONTRACT, EVENT_LOG, EVENT_ORDER,
        EVENT_POSITION, EVENT_TICK, EVENT_TRADE,
    )
    from vnpy.trader.object import CancelRequest, OrderRequest, SubscribeRequest
    from vnpy_ctp import CtpGateway
    VNPY_AVAILABLE = True
except Exception:
    VNPY_AVAILABLE = False
    logger.warning("vnpy/vnpy_ctp 未安装或导入失败")


def _safe_enum_value(value: Any) -> str:
    return str(value.value) if hasattr(value, "value") else str(value or "")


_DIR_ZH = {"LONG": "多", "Long": "多", "多": "多", "BUY": "多", "Buy": "多",
           "SHORT": "空", "Short": "空", "空": "空", "SELL": "空", "Sell": "空",
           "NET": "净", "Net": "净", "净": "净"}

_OFFSET_ZH = {"OPEN": "开", "Open": "开", "开": "开",
              "CLOSE": "平", "Close": "平", "平": "平",
              "CLOSETODAY": "平今", "CloseToday": "平今", "平今": "平今",
              "CLOSEYESTERDAY": "平昨", "CloseYesterday": "平昨", "平昨": "平昨"}

_EXCHANGE_ALIASES = {
    "ZCE": "CZCE",
    "CZCE": "CZCE",
    "DCE": "DCE",
    "SHFE": "SHFE",
    "INE": "INE",
    "CFFEX": "CFFEX",
    "GFEX": "GFEX",
}

# vnpy 订单状态枚举映射（中文 value）。我们以字符串匹配，避免对 vnpy 内部枚举对象的强依赖。
_ORDER_STATUS_FILLED = {"全部成交", "ALLTRADED"}
_ORDER_STATUS_REJECTED = {"拒单", "REJECTED"}
_ORDER_STATUS_CANCELLED = {"已撤销", "CANCELLED"}
_ORDER_STATUS_PARTIAL = {"部分成交", "PARTTRADED"}
_ORDER_STATUS_ACTIVE = {"提交中", "未成交", "SUBMITTING", "NOTTRADED"}


def _zh_direction(raw: str) -> str:
    return _DIR_ZH.get(raw, raw)


def _zh_offset(raw: str) -> str:
    return _OFFSET_ZH.get(raw, raw)


class CTPClient:
    """In-process CTP gateway wrapper using vnpy."""

    _TRADE_STORE = Path(__file__).resolve().parent.parent / "data" / "ctp_trades.json"

    def __init__(self) -> None:
        self._phase = "idle"
        self._event_engine = None
        self._main_engine = None
        self._gateway_name = "CTP"
        self._settings_used: dict = {}

        self._tick_buffer: deque[dict] = deque(maxlen=500)
        self._order_list: deque[dict] = deque(maxlen=200)
        self._trade_list: deque[dict] = deque(maxlen=200)
        self._order_index: dict[str, dict] = {}
        self._order_event = threading.Event()
        self._position_cache: dict[str, dict] = {}
        self._account_cache: dict[str, Any] = {}
        self._contract_map: dict[str, str] = {}
        self._contract_symbol_map: dict[str, str] = {}
        self._subscribed: set[str] = set()
        self._auto_execute = False
        self._pending_signals: deque[dict] = deque(maxlen=50)
        # Most recent CTP gateway-level error message (e.g. "委托请求发送失败，
        # 错误代码：-1" / "资金不足…"). Attached to order results when
        # ``send_order`` returns no vt_orderid.
        self._last_gateway_error: str = ""
        self._last_gateway_error_ts: float = 0.0

        self._cmd_queue: queue.Queue = queue.Queue()
        self._cmd_thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None
        self._sync_stop = threading.Event()

        self._on_tick_callbacks: list = []

        self._load_persisted_trades()

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def connected(self) -> bool:
        return self._phase in ("gateway_up", "ready")

    @property
    def ready(self) -> bool:
        return self._phase == "ready"

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def connect(self, connect_info: dict) -> dict:
        if not VNPY_AVAILABLE:
            raise RuntimeError("vnpy/vnpy_ctp 未安装")
        if self._phase != "idle":
            return {"status": "already_connected", "phase": self._phase}

        self._reset()
        self._phase = "connecting"
        self._start_cmd_thread()

        self._event_engine = EventEngine()
        self._main_engine = MainEngine(self._event_engine)
        self._main_engine.add_gateway(CtpGateway)
        self._register_handlers()

        self._gateway_name = getattr(CtpGateway, "default_name", "CTP")
        self._settings_used = self._build_setting(connect_info)

        self._exec_cmd(self._main_engine.connect, self._settings_used, self._gateway_name, timeout=12)
        self._start_sync_thread()

        for _ in range(60):
            time.sleep(0.5)
            if self._phase == "ready":
                break
            if self._phase == "connecting":
                if self._contract_map or self._read_account() is not None:
                    self._phase = "gateway_up"

        if self._phase in ("gateway_up", "ready"):
            logger.info("CTP 连接成功 (phase=%s, 合约=%d)", self._phase, len(self._contract_map))
            self._prefill_trades_from_engine()
            return {"status": "connected", "phase": self._phase}

        self._phase = "idle"
        return {"status": "timeout", "error": "网关登录超时"}

    def disconnect(self) -> dict:
        self._sync_stop.set()
        if self._sync_thread:
            self._sync_thread.join(timeout=3)
            self._sync_thread = None
        if self._main_engine:
            try:
                self._main_engine.close()
            except Exception:
                pass
            self._main_engine = None
            self._event_engine = None
        self._stop_cmd_thread()
        self._reset()
        logger.info("CTP 已断开")
        return {"status": "disconnected"}

    # ------------------------------------------------------------------
    # Trading operations
    # ------------------------------------------------------------------

    def subscribe(self, symbols: list[str]) -> dict:
        self._require_connected()
        results = []
        for raw in symbols:
            symbol, ex_hint = self._split_symbol(raw)
            ex_obj = self._parse_exchange(symbol, ex_hint)
            if not ex_obj:
                results.append({"symbol": symbol, "ok": False, "error": "无法推断交易所"})
                continue
            self._subscribed.add(symbol)
            try:
                req = SubscribeRequest(symbol=symbol, exchange=ex_obj)
                self._exec_cmd(self._main_engine.subscribe, req, self._gateway_name, timeout=5)
                results.append({"symbol": symbol, "exchange": ex_obj.value, "ok": True})
            except Exception as e:
                results.append({"symbol": symbol, "ok": False, "error": str(e)})
        return {"subscribed": list(self._subscribed), "results": results}

    def unsubscribe(self, symbols: list[str]) -> dict:
        removed = []
        for raw in symbols:
            symbol, _ = self._split_symbol(raw)
            self._subscribed.discard(symbol)
            removed.append(symbol)
        return {"unsubscribed": removed, "subscribed": list(self._subscribed)}

    def place_order(self, symbol: str, direction: str, offset: str,
                    price: float, volume: int = 1,
                    exchange: str = "",
                    wait_seconds: float = 0.0) -> dict:
        """Submit a limit order; optionally block until terminal status.

        Parameters
        ----------
        wait_seconds:
            If > 0, block up to this many seconds waiting for the order to reach a
            terminal state (filled / rejected / cancelled). The returned status
            reflects the latest known state.

        Returns dict with: status, order_id, symbol, exchange,
        traded_volume, total_volume, reject_reason (when rejected).
        Status values:
            submitted    委托已提交 (尚未确认成交; 调用方应继续监听)
            pending      已提交但等待超时仍未成交
            filled       全部成交 (本会话内确认)
            part_traded  部分成交 (剩余仍在挂单或已撤)
            rejected     拒单
            cancelled    已撤销
        """
        self._require_ready()
        sym, sym_ex = self._split_symbol(symbol)
        exchange_hint = sym_ex or exchange
        sym = self._resolve_contract_symbol(sym, exchange_hint)
        if not self._has_contract_symbol(sym):
            raise ValueError(f"合约未在CTP合约列表中: {sym}")
        ex_obj = self._parse_exchange(sym, exchange_hint)
        if not ex_obj:
            raise ValueError("无法识别交易所")

        req = OrderRequest(
            symbol=sym, exchange=ex_obj,
            direction=self._parse_direction(direction),
            offset=self._parse_offset(offset),
            type=OrderType.LIMIT, price=price, volume=volume,
            reference="Finme",
        )
        # Clear any stale gateway-level error from a previous attempt so we
        # only surface errors caused by *this* send_order call.
        self._last_gateway_error = ""
        self._last_gateway_error_ts = 0.0
        send_start = time.time()
        try:
            vt_orderid = self._exec_cmd(
                self._main_engine.send_order, req, self._gateway_name, timeout=8,
            )
        except Exception as exc:
            logger.error("下单异常: %s %s %s.%s @ %s — %s",
                         direction, offset, sym, ex_obj.value, price, exc)
            return {
                "status": "failed",
                "order_id": "",
                "symbol": sym,
                "exchange": ex_obj.value,
                "total_volume": float(volume),
                "traded_volume": 0.0,
                "reject_reason": str(exc),
            }

        logger.info("下单: %s %s %s.%s @ %s x %d, id=%s",
                    direction, offset, sym, ex_obj.value, price, volume, vt_orderid)

        # vnpy's CTP gateway returns an empty string (or None) when the
        # order never reached the server — typically outside trading hours
        # ("委托请求发送失败，错误代码：-1") or due to a margin / risk check
        # failure. Surface it as a terminal failure so callers don't think
        # the order is "submitted" and keep waiting for fills that never
        # come.
        if not vt_orderid:
            # Give the gateway a brief moment to emit the EVENT_LOG error
            # message so we can include it in the reason.
            for _ in range(10):
                if self._last_gateway_error and self._last_gateway_error_ts >= send_start:
                    break
                time.sleep(0.05)
            reason = (self._last_gateway_error
                      or "委托请求发送失败 (vnpy 未返回 orderid)")
            logger.error("下单失败: %s %s %s.%s @ %s — %s",
                         direction, offset, sym, ex_obj.value, price, reason)
            return {
                "status": "failed",
                "order_id": "",
                "symbol": sym,
                "exchange": ex_obj.value,
                "total_volume": float(volume),
                "traded_volume": 0.0,
                "reject_reason": reason,
            }

        result = {
            "status": "submitted",
            "order_id": vt_orderid,
            "symbol": sym,
            "exchange": ex_obj.value,
            "total_volume": float(volume),
            "traded_volume": 0.0,
        }

        if wait_seconds and wait_seconds > 0 and vt_orderid:
            terminal = self._wait_order_terminal(vt_orderid, wait_seconds)
            if terminal:
                result.update(terminal)
                if terminal.get("status") == "rejected":
                    logger.warning("下单被拒: id=%s reason=%s",
                                   vt_orderid, terminal.get("reject_reason", ""))
                elif terminal.get("status") == "filled":
                    logger.info("下单成交: id=%s 成交量=%s/%s",
                                vt_orderid,
                                terminal.get("traded_volume"),
                                terminal.get("total_volume"))
                elif terminal.get("status") == "part_traded":
                    logger.info("下单部分成交: id=%s 成交量=%s/%s",
                                vt_orderid,
                                terminal.get("traded_volume"),
                                terminal.get("total_volume"))
                elif terminal.get("status") == "cancelled":
                    logger.warning("下单已撤销: id=%s", vt_orderid)
                elif terminal.get("status") == "pending":
                    logger.warning("下单等待成交超时: id=%s 已成交=%s/%s, 仍挂单中",
                                   vt_orderid,
                                   terminal.get("traded_volume"),
                                   terminal.get("total_volume"))
        return result

    # ------------------------------------------------------------------
    # Order status query helpers
    # ------------------------------------------------------------------

    def get_order_snapshot(self, order_id: str) -> dict | None:
        """Return the most recent dict snapshot of a known order, if any."""
        if not order_id:
            return None
        rec = self._order_index.get(order_id)
        if rec:
            return dict(rec)
        if self._main_engine:
            try:
                order = self._main_engine.get_order(order_id)
            except Exception:
                order = None
            if order:
                return self._order_to_dict(order)
        return None

    def wait_order_terminal(self, order_id: str, timeout: float = 3.0) -> dict:
        """Public version of :meth:`_wait_order_terminal`."""
        return self._wait_order_terminal(order_id, timeout) or {
            "status": "pending", "traded_volume": 0.0, "total_volume": 0.0,
        }

    def has_position(self, symbol: str, direction: str,
                     exchange_hint: str = "") -> bool:
        """Return True if CTP holds at least 1 lot on ``symbol`` in ``direction``."""
        return self.get_position_volume(symbol, direction, exchange_hint) > 0

    def get_active_orders(self, symbol: str, direction: str = "",
                          offset: str = "",
                          exchange_hint: str = "") -> list[dict]:
        """Return all non-terminal orders for ``symbol`` (optionally filtered
        by ``direction`` / ``offset``).

        Used to prevent the exact failure mode the user reported: once a
        limit order has been submitted and is sitting unfilled, CTP has
        already locked the margin for it. A second submit on the same
        contract/direction comes back as ``资金不足`` because the account's
        *available* balance no longer covers another lot. So we skip the
        new submit and wait for the existing one to fill / cancel instead.
        """
        raw_sym = (symbol or "").split(".", 1)[0].upper()
        resolved = self._resolve_contract_symbol(symbol, exchange_hint)
        resolved_bare = (resolved or "").split(".", 1)[0].upper()
        # ``_order_to_dict`` stores ``symbol`` without exchange suffix, so
        # we match on the bare contract code (``SR609``).
        sym_u = resolved_bare or raw_sym

        want_dir = ""
        if direction:
            dl = direction.lower()
            want_dir = "多" if dl in ("long", "buy", "多") else "空"

        want_off = ""
        if offset:
            ol = offset.lower()
            want_off = "开" if ol in ("open", "开") else "平"

        result: list[dict] = []
        for rec in self._order_index.values():
            r_sym_full = (rec.get("symbol") or "").upper()
            r_sym_bare = r_sym_full.split(".", 1)[0]
            if r_sym_bare != sym_u:
                continue
            status = str(rec.get("status") or "")
            if (status in _ORDER_STATUS_FILLED
                    or status in _ORDER_STATUS_REJECTED
                    or status in _ORDER_STATUS_CANCELLED):
                continue
            if want_dir and rec.get("direction") != want_dir:
                continue
            if want_off and rec.get("offset") != want_off:
                continue
            result.append(dict(rec))
        return result

    def has_active_order(self, symbol: str, direction: str = "",
                         offset: str = "", exchange_hint: str = "") -> bool:
        return bool(self.get_active_orders(symbol, direction, offset, exchange_hint))

    def get_position_volume(self, symbol: str, direction: str,
                            exchange_hint: str = "") -> float:
        sym = self._resolve_contract_symbol(symbol, exchange_hint)
        sym_upper = sym.upper()
        try:
            positions = self.get_positions()
        except Exception:
            return 0.0
        dir_long = direction in ("long", "做多", "buy", "BUY", "Long", "LONG", "多")
        for pos in positions:
            p_sym = (pos.get("symbol") or "").upper()
            p_dir = (pos.get("direction") or "").lower()
            p_vol = float(pos.get("volume") or 0)
            if not (p_sym == sym_upper or p_sym.startswith(sym_upper + ".")):
                continue
            dir_match = (
                (dir_long and p_dir in ("long", "多", "净"))
                or (not dir_long and p_dir in ("short", "空", "净"))
            )
            if dir_match and p_vol > 0:
                return p_vol
        return 0.0

    def _wait_order_terminal(self, order_id: str, timeout: float) -> dict | None:
        """Block (with cooperative event) until the order is terminal.

        Returns dict with ``status``, ``traded_volume``, ``total_volume``,
        and optional ``reject_reason``. When timeout is hit and the order is
        still active, returns status ``pending`` (or ``part_traded`` if some
        lots have been filled).
        """
        deadline = time.time() + max(0.05, timeout)
        last: dict | None = None
        while True:
            snap = self.get_order_snapshot(order_id)
            if snap:
                last = snap
                norm = self._normalize_order_status(snap)
                if norm in ("filled", "rejected", "cancelled"):
                    return self._build_terminal_result(snap, norm)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self._order_event.clear()
            self._order_event.wait(timeout=min(remaining, 0.5))

        if last is None:
            return {"status": "pending", "traded_volume": 0.0, "total_volume": 0.0}

        norm = self._normalize_order_status(last)
        if norm == "part_traded":
            return self._build_terminal_result(last, "part_traded")
        return self._build_terminal_result(last, "pending")

    @staticmethod
    def _normalize_order_status(snap: dict) -> str:
        status = str(snap.get("status") or "")
        traded = float(snap.get("traded") or 0)
        total = float(snap.get("volume") or 0)
        if status in _ORDER_STATUS_FILLED or (total > 0 and traded >= total):
            return "filled"
        if status in _ORDER_STATUS_REJECTED:
            return "rejected"
        if status in _ORDER_STATUS_CANCELLED:
            return "cancelled"
        if status in _ORDER_STATUS_PARTIAL or (traded > 0 and traded < total):
            return "part_traded"
        return "active"

    @staticmethod
    def _build_terminal_result(snap: dict, norm: str) -> dict:
        return {
            "status": norm,
            "traded_volume": float(snap.get("traded") or 0),
            "total_volume": float(snap.get("volume") or 0),
            "raw_status": snap.get("status", ""),
            "reject_reason": snap.get("reject_reason", ""),
        }

    def cancel_order(self, order_id: str) -> dict:
        self._require_ready()
        order_data = self._main_engine.get_order(order_id)
        if not order_data:
            for rec in reversed(self._order_list):
                if rec.get("vt_orderid") == order_id or rec.get("order_id") == order_id:
                    order_data = self._main_engine.get_order(rec.get("vt_orderid", ""))
                    break
        if not order_data:
            raise ValueError(f"找不到委托: {order_id}")
        req = order_data.create_cancel_request()
        self._exec_cmd(self._main_engine.cancel_order, req,
                       getattr(order_data, "gateway_name", self._gateway_name), timeout=6)
        return {"status": "cancel_submitted", "order_id": order_id}

    def resolve_contract_symbol(self, symbol: str, exchange: str = "") -> str:
        """Return the exact contract symbol loaded from CTP, if available."""
        return self._resolve_contract_symbol(symbol, exchange)

    def get_positions(self) -> list[dict]:
        if self._main_engine:
            try:
                raw = [self._pos_to_dict(p) for p in self._main_engine.get_all_positions()
                       if (getattr(p, "volume", 0) or 0) > 0]
                return self._enrich_positions_with_trade_prices(raw)
            except Exception:
                pass
        return self._enrich_positions_with_trade_prices(list(self._position_cache.values()))

    def _enrich_positions_with_trade_prices(self, positions: list[dict]) -> list[dict]:
        """Replace CTP settlement-based avg price with actual entry price from trades.

        CTP's position.price is often the previous settlement price for
        yesterday positions, not the actual entry price. We recalculate from
        open-trade records for accuracy.
        """
        open_trades: dict[str, list[dict]] = {}
        for t in self._trade_list:
            if t.get("offset") not in ("开", "OPEN", "Open"):
                continue
            key = f'{t["symbol"]}_{t["direction"]}'
            open_trades.setdefault(key, []).append(t)

        if not open_trades:
            return positions

        held_keys: set[str] = set()
        for pos in positions:
            key = f'{pos["symbol"]}_{pos["direction"]}'
            held_keys.add(key)
            trades = open_trades.get(key)
            if not trades:
                continue
            total_vol = 0.0
            total_cost = 0.0
            for t in trades:
                v = float(t.get("volume", 0))
                p = float(t.get("price", 0))
                total_vol += v
                total_cost += v * p
            if total_vol > 0:
                pos["price"] = round(total_cost / total_vol, 2)

        stale = [k for k in open_trades if k not in held_keys]
        if stale:
            self._persist_trades()

        return positions

    def get_account(self) -> dict | None:
        if self._account_cache.get("account_id"):
            return dict(self._account_cache)
        if self._main_engine:
            try:
                accounts = self._main_engine.get_all_accounts()
                if accounts:
                    a = accounts[0]
                    return {
                        "account_id": getattr(a, "accountid", ""),
                        "balance": float(getattr(a, "balance", 0) or 0),
                        "available": float(getattr(a, "available", 0) or 0),
                        "frozen": float(getattr(a, "frozen", 0) or 0),
                    }
            except Exception:
                pass
        return None

    def get_orders(self, limit: int = 50) -> list[dict]:
        return list(self._order_list)[-limit:]

    def get_trades(self, limit: int = 50) -> list[dict]:
        return list(self._trade_list)[-limit:]

    def get_ticks(self, symbol: str = "", limit: int = 50) -> list[dict]:
        ticks = list(self._tick_buffer)
        if symbol:
            sym = self._normalize(symbol)
            ticks = [t for t in ticks if self._normalize(t.get("symbol", "")) == sym]
        return ticks[-limit:]

    def get_status(self) -> dict:
        return {
            "connected": self.connected,
            "phase": self._phase,
            "trader_ready": self.ready,
            "subscribed": list(self._subscribed),
            "auto_execute": self._auto_execute,
            "account": self.get_account() if self.connected else None,
            "positions": self.get_positions() if self.connected else [],
        }

    def set_auto_execute(self, enabled: bool) -> None:
        self._auto_execute = enabled

    def health(self) -> dict:
        return {
            "phase": self._phase,
            "connected": self.connected,
            "ready": self.ready,
            "vnpy_available": VNPY_AVAILABLE,
            "subscribed_count": len(self._subscribed),
            "contract_count": len(self._contract_map),
        }

    def execute_v14_signal(self, signal: dict) -> dict:
        """Execute a V14 trading signal (开多/开空/平多/平空)."""
        self._require_ready()

        signal_type = signal.get("signal_type", "")
        symbol = signal.get("symbol", "")
        price = float(signal.get("price", 0))
        volume = int(signal.get("volume", 1))
        exchange_hint = signal.get("exchange", "")

        if not symbol or not signal_type:
            raise ValueError("缺少 symbol/signal_type")

        signal_record: dict = {
            "signal_type": signal_type,
            "symbol": symbol,
            "price": price,
            "volume": volume,
            "stop_loss": float(signal.get("stop_loss", 0)),
            "take_profit": float(signal.get("take_profit", 0)),
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "executed": False,
        }

        signal_map = {
            "开多": ("long", "open"),
            "开空": ("short", "open"),
            "平多": ("short", "close"),
            "平空": ("long", "close"),
        }

        matched = None
        for prefix, mapping in signal_map.items():
            if signal_type.startswith(prefix):
                matched = mapping
                break

        if not matched:
            signal_record["error"] = f"未知信号: {signal_type}"
            self._pending_signals.append(signal_record)
            return {"status": "unknown_signal", "signal": signal_record}

        try:
            sym, sym_ex = self._split_symbol(symbol)
            ex_hint = sym_ex or exchange_hint
            sym = self._resolve_contract_symbol(sym, ex_hint)
            if not self._has_contract_symbol(sym):
                raise ValueError(f"合约未在CTP合约列表中: {sym}")
            ex_obj = self._parse_exchange(sym, ex_hint)
            if not ex_obj:
                raise ValueError("无法识别交易所，请提供 exchange")

            req = OrderRequest(
                symbol=sym, exchange=ex_obj,
                direction=self._parse_direction(matched[0]),
                offset=self._parse_offset(matched[1]),
                type=OrderType.LIMIT, price=price, volume=volume,
                reference="FinmeV14",
            )
            vt_orderid = self._exec_cmd(self._main_engine.send_order, req, self._gateway_name, timeout=8)
            signal_record["executed"] = True
            signal_record["order_id"] = vt_orderid
            signal_record["vt_orderid"] = vt_orderid
            self._pending_signals.append(signal_record)

            logger.info("V14 信号: %s %s.%s @ %s x %d", signal_type, sym, ex_obj.value, price, volume)
            return {"status": "executed", "signal": signal_record}

        except Exception as e:
            signal_record["error"] = str(e)
            self._pending_signals.append(signal_record)
            raise

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        if not self._event_engine:
            return
        self._event_engine.register(EVENT_LOG, self._on_log)
        self._event_engine.register(EVENT_TICK, self._on_tick)
        self._event_engine.register(EVENT_ORDER, self._on_order)
        self._event_engine.register(EVENT_TRADE, self._on_trade)
        self._event_engine.register(EVENT_POSITION, self._on_position)
        self._event_engine.register(EVENT_ACCOUNT, self._on_account)
        self._event_engine.register(EVENT_CONTRACT, self._on_contract)

    def _on_log(self, event) -> None:
        msg = getattr(event.data, "msg", "") if event.data else ""
        if not msg:
            return
        logger.debug("[CTP] %s", msg)
        if self._phase in ("connecting", "gateway_up"):
            if any(k in msg for k in ("结算信息确认成功", "合约信息查询成功")):
                self._phase = "ready"
            elif "登录成功" in msg or "连接成功" in msg:
                if self._phase == "connecting":
                    self._phase = "gateway_up"
        # 捕捉与委托相关的拒单/错误日志，挂到最近一笔活跃订单上以便定位
        order_keywords = ("拒单", "委托失败", "报单失败", "撤单失败",
                          "委托请求发送失败", "资金不足",
                          "rejected", "Rejected", "REJECTED",
                          "错误代码", "ErrorID")
        if any(k in msg for k in order_keywords):
            # Latch as gateway-level error so place_order() can surface it
            # when vnpy returns an empty vt_orderid.
            self._last_gateway_error = msg
            self._last_gateway_error_ts = time.time()
            self._attach_reject_reason(msg)
            self._order_event.set()
            logger.warning("[CTP] %s", msg)

    def _attach_reject_reason(self, msg: str) -> None:
        try:
            for vt_id in reversed(list(self._order_index.keys())):
                rec = self._order_index.get(vt_id) or {}
                status = str(rec.get("status") or "")
                if status in _ORDER_STATUS_FILLED or status in _ORDER_STATUS_CANCELLED:
                    continue
                if not rec.get("reject_reason"):
                    rec["reject_reason"] = msg
                    self._order_index[vt_id] = rec
                    self._order_event.set()
                    return
        except Exception:
            pass

    def _on_tick(self, event) -> None:
        tick = event.data
        try:
            d = {
                "symbol": getattr(tick, "symbol", ""),
                "exchange": _safe_enum_value(getattr(tick, "exchange", "")),
                "datetime": tick.datetime.strftime("%Y-%m-%d %H:%M:%S") if getattr(tick, "datetime", None) else "",
                "last_price": float(getattr(tick, "last_price", 0) or 0),
                "open_price": float(getattr(tick, "open_price", 0) or 0),
                "high_price": float(getattr(tick, "high_price", 0) or 0),
                "low_price": float(getattr(tick, "low_price", 0) or 0),
                "pre_close": float(getattr(tick, "pre_close", 0) or 0),
                "volume": float(getattr(tick, "volume", 0) or 0),
                "open_interest": float(getattr(tick, "open_interest", 0) or 0),
                "turnover": float(getattr(tick, "turnover", 0) or 0),
                "bid_price_1": float(getattr(tick, "bid_price_1", 0) or 0),
                "bid_volume_1": float(getattr(tick, "bid_volume_1", 0) or 0),
                "ask_price_1": float(getattr(tick, "ask_price_1", 0) or 0),
                "ask_volume_1": float(getattr(tick, "ask_volume_1", 0) or 0),
            }
            self._tick_buffer.append(d)
            for cb in self._on_tick_callbacks:
                try:
                    cb(d)
                except Exception:
                    pass
        except Exception as e:
            logger.error("on_tick error: %s", e)

    def _on_order(self, event) -> None:
        order = event.data
        try:
            rec = self._order_to_dict(order)
            vt_id = rec.get("vt_orderid") or rec.get("order_id")
            prev = self._order_index.get(vt_id) if vt_id else None
            if vt_id:
                self._order_index[vt_id] = rec
            self._order_list.append(rec)

            prev_status = prev.get("status") if prev else None
            prev_traded = float(prev.get("traded") or 0) if prev else -1.0
            cur_status = rec.get("status") or ""
            cur_traded = float(rec.get("traded") or 0)
            if prev is None or prev_status != cur_status or prev_traded != cur_traded:
                logger.info(
                    "[CTP] 委托更新: id=%s %s %s %s.%s 价=%s 量=%s 已成=%s 状态=%s",
                    vt_id,
                    rec.get("direction", ""),
                    rec.get("offset", ""),
                    rec.get("symbol", ""),
                    rec.get("exchange", ""),
                    rec.get("price"),
                    rec.get("volume"),
                    rec.get("traded"),
                    cur_status,
                )
                if cur_status in _ORDER_STATUS_REJECTED:
                    logger.error(
                        "[CTP] 委托被拒: id=%s 合约=%s 原因=%s",
                        vt_id, rec.get("symbol", ""),
                        rec.get("reject_reason") or cur_status,
                    )
            self._order_event.set()
        except Exception as e:
            logger.error("on_order error: %s", e)

    def _on_trade(self, event) -> None:
        trade = event.data
        try:
            rec = {
                "trade_id": getattr(trade, "tradeid", ""),
                "order_id": getattr(trade, "orderid", ""),
                "vt_orderid": getattr(trade, "vt_orderid", ""),
                "symbol": getattr(trade, "symbol", ""),
                "exchange": _safe_enum_value(getattr(trade, "exchange", "")),
                "direction": _zh_direction(_safe_enum_value(getattr(trade, "direction", ""))),
                "offset": _zh_offset(_safe_enum_value(getattr(trade, "offset", ""))),
                "price": float(getattr(trade, "price", 0) or 0),
                "volume": float(getattr(trade, "volume", 0) or 0),
                "datetime": (getattr(trade, "datetime", None).strftime("%Y-%m-%d %H:%M:%S")
                             if getattr(trade, "datetime", None) else datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            }
            self._trade_list.append(rec)
            logger.info(
                "[CTP] 成交: id=%s %s %s %s.%s 价=%s 量=%s",
                rec.get("vt_orderid") or rec.get("order_id"),
                rec.get("direction", ""),
                rec.get("offset", ""),
                rec.get("symbol", ""),
                rec.get("exchange", ""),
                rec.get("price"),
                rec.get("volume"),
            )
            if rec["offset"] in ("开", "OPEN", "Open"):
                self._persist_trades()
            # 触发等待方
            self._order_event.set()
        except Exception as e:
            logger.error("on_trade error: %s", e)

    @staticmethod
    def _order_to_dict(order) -> dict:
        return {
            "order_id": getattr(order, "orderid", ""),
            "vt_orderid": getattr(order, "vt_orderid", ""),
            "symbol": getattr(order, "symbol", ""),
            "exchange": _safe_enum_value(getattr(order, "exchange", "")),
            "direction": _zh_direction(_safe_enum_value(getattr(order, "direction", ""))),
            "offset": _zh_offset(_safe_enum_value(getattr(order, "offset", ""))),
            "price": float(getattr(order, "price", 0) or 0),
            "volume": float(getattr(order, "volume", 0) or 0),
            "traded": float(getattr(order, "traded", 0) or 0),
            "status": _safe_enum_value(getattr(order, "status", "")),
            "reject_reason": "",
            "datetime": (getattr(order, "datetime", None).strftime("%Y-%m-%d %H:%M:%S")
                         if getattr(order, "datetime", None)
                         else datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        }

    def _on_position(self, event) -> None:
        pos = event.data
        try:
            d = self._pos_to_dict(pos)
            key = d.get("vt_positionid") or f'{d["symbol"]}_{d["direction"]}'
            self._position_cache[key] = d
            if self._phase in ("connecting", "gateway_up"):
                self._phase = "ready"
        except Exception as e:
            logger.error("on_position error: %s", e)

    def _on_account(self, event) -> None:
        acc = event.data
        try:
            self._account_cache.update({
                "account_id": getattr(acc, "accountid", ""),
                "balance": float(getattr(acc, "balance", 0) or 0),
                "available": float(getattr(acc, "available", 0) or 0),
                "frozen": float(getattr(acc, "frozen", 0) or 0),
            })
        except Exception as e:
            logger.error("on_account error: %s", e)

    def _on_contract(self, event) -> None:
        c = event.data
        try:
            sym = getattr(c, "symbol", "")
            ex = self._normalize_exchange(_safe_enum_value(getattr(c, "exchange", "")))
            if sym and ex:
                for key in {sym, sym.lower(), sym.upper()}:
                    self._contract_map[key] = ex
                    self._contract_symbol_map[key] = sym
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._phase = "idle"
        self._tick_buffer.clear()
        self._order_list.clear()
        self._trade_list.clear()
        self._order_index.clear()
        self._order_event.clear()
        self._position_cache.clear()
        self._account_cache.clear()
        self._contract_map.clear()
        self._contract_symbol_map.clear()
        self._subscribed.clear()
        self._pending_signals.clear()

    # ------------------------------------------------------------------
    # Trade persistence (survive restarts for accurate open prices)
    # ------------------------------------------------------------------

    def _load_persisted_trades(self) -> None:
        try:
            if self._TRADE_STORE.exists():
                data = json.loads(self._TRADE_STORE.read_text(encoding="utf-8"))
                for t in data:
                    self._trade_list.append(t)
                logger.info("已加载 %d 条持久化成交记录", len(data))
        except Exception as e:
            logger.warning("加载持久化成交记录失败: %s", e)

    def _persist_trades(self) -> None:
        try:
            self._TRADE_STORE.parent.mkdir(parents=True, exist_ok=True)
            open_trades = [t for t in self._trade_list
                           if t.get("offset") in ("开", "OPEN", "Open")]
            self._TRADE_STORE.write_text(
                json.dumps(open_trades, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            logger.warning("持久化成交记录失败: %s", e)

    def _prefill_trades_from_engine(self) -> None:
        """Query all trades from CTP for the current trading day."""
        if not self._main_engine:
            return
        try:
            all_trades = self._main_engine.get_all_trades()
            if not all_trades:
                return
            existing_ids = {t.get("trade_id") for t in self._trade_list}
            count = 0
            for trade in all_trades:
                tid = getattr(trade, "tradeid", "")
                if tid in existing_ids:
                    continue
                self._trade_list.append({
                    "trade_id": tid,
                    "order_id": getattr(trade, "orderid", ""),
                    "symbol": getattr(trade, "symbol", ""),
                    "exchange": _safe_enum_value(getattr(trade, "exchange", "")),
                    "direction": _zh_direction(_safe_enum_value(getattr(trade, "direction", ""))),
                    "offset": _zh_offset(_safe_enum_value(getattr(trade, "offset", ""))),
                    "price": float(getattr(trade, "price", 0) or 0),
                    "volume": float(getattr(trade, "volume", 0) or 0),
                    "datetime": (getattr(trade, "datetime", None).strftime("%Y-%m-%d %H:%M:%S")
                                 if getattr(trade, "datetime", None)
                                 else datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                })
                count += 1
            if count:
                logger.info("从CTP预填充 %d 条当日成交记录", count)
                self._persist_trades()
        except Exception as e:
            logger.warning("预填充当日成交记录失败: %s", e)

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("CTP 未连接")

    def _require_ready(self) -> None:
        if not self.ready:
            raise RuntimeError(f"CTP 未就绪 (phase={self._phase})")

    def _read_account(self) -> dict | None:
        if self._account_cache.get("account_id"):
            return dict(self._account_cache)
        return None

    # ------------------------------------------------------------------
    # Command queue
    # ------------------------------------------------------------------

    def _start_cmd_thread(self) -> None:
        if self._cmd_thread and self._cmd_thread.is_alive():
            return
        self._cmd_thread = threading.Thread(target=self._cmd_worker, daemon=True, name="ctp-cmd")
        self._cmd_thread.start()

    def _stop_cmd_thread(self) -> None:
        if self._cmd_thread:
            self._cmd_queue.put(None)
            self._cmd_thread.join(timeout=3)
            self._cmd_thread = None

    def _cmd_worker(self) -> None:
        while True:
            item = self._cmd_queue.get()
            if item is None:
                break
            fn, args, kwargs, evt, holder = item
            try:
                holder["value"] = fn(*args, **kwargs)
            except Exception as e:
                holder["error"] = e
            finally:
                evt.set()
                self._cmd_queue.task_done()

    def _exec_cmd(self, fn, *args, timeout: int = 10, **kwargs):
        holder: dict = {"value": None, "error": None}
        evt = threading.Event()
        self._cmd_queue.put((fn, args, kwargs, evt, holder))
        if not evt.wait(timeout=timeout):
            raise TimeoutError("CTP 命令超时")
        if holder["error"]:
            raise holder["error"]
        return holder["value"]

    def _start_sync_thread(self) -> None:
        self._sync_stop.clear()
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._sync_thread = threading.Thread(target=self._sync_worker, daemon=True, name="ctp-sync")
        self._sync_thread.start()

    def _sync_worker(self) -> None:
        while not self._sync_stop.is_set():
            try:
                if self._phase in ("gateway_up", "ready") and self._main_engine:
                    gw = self._main_engine.get_gateway(self._gateway_name)
                    if gw:
                        if hasattr(gw, "query_account"):
                            gw.query_account()
                        if hasattr(gw, "query_position"):
                            gw.query_position()
            except Exception:
                pass
            self._sync_stop.wait(2.0)

    # ------------------------------------------------------------------
    # Parsing utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _pos_to_dict(pos) -> dict:
        return {
            "symbol": getattr(pos, "symbol", ""),
            "exchange": _safe_enum_value(getattr(pos, "exchange", "")),
            "direction": _zh_direction(_safe_enum_value(getattr(pos, "direction", ""))),
            "volume": float(getattr(pos, "volume", 0) or 0),
            "yd_volume": float(getattr(pos, "yd_volume", 0) or 0),
            "frozen": float(getattr(pos, "frozen", 0) or 0),
            "price": float(getattr(pos, "price", 0) or 0),
            "pnl": float(getattr(pos, "pnl", 0) or 0),
            "vt_positionid": getattr(pos, "vt_positionid", ""),
            "vt_symbol": getattr(pos, "vt_symbol", ""),
            "gateway_name": getattr(pos, "gateway_name", ""),
        }

    @staticmethod
    def _normalize(sym: str) -> str:
        return sym.split(".")[0].strip()

    @staticmethod
    def _split_symbol(raw: str) -> tuple[str, str]:
        s = raw.strip()
        if "." in s:
            left, right = s.split(".", 1)
            return left, CTPClient._normalize_exchange(right)
        return s, ""

    def _resolve_contract_symbol(self, symbol: str, exchange_hint: str = "") -> str:
        """Use CTP's loaded contract spelling before sending orders."""
        if not symbol:
            return symbol
        for candidate in self._contract_symbol_candidates(symbol, exchange_hint):
            actual = self._contract_symbol_map.get(candidate)
            if actual:
                return actual
        return symbol

    def _has_contract_symbol(self, symbol: str) -> bool:
        return bool(symbol and symbol in self._contract_map)

    @staticmethod
    def _normalize_exchange(name: str) -> str:
        key = str(name or "").upper()
        return _EXCHANGE_ALIASES.get(key, key)

    def _contract_symbol_candidates(self, symbol: str, exchange_hint: str = "") -> list[str]:
        candidates: list[str] = []

        def add(value: str) -> None:
            if value and value not in candidates:
                candidates.append(value)

        add(symbol)
        add(symbol.lower())
        add(symbol.upper())

        exchange = self._normalize_exchange(exchange_hint)
        product = "".join(ch for ch in symbol if ch.isalpha())
        month = symbol[len(product):]
        if exchange == "CZCE" and len(month) == 4 and month.isdigit():
            # Tushare uses SR2609, while CTP/vn.py often loads CZCE as SR609.
            zce_symbol = f"{product.upper()}{month[1:]}"
            add(zce_symbol)
            add(zce_symbol.lower())

        return candidates

    def _parse_exchange(self, symbol: str, hint: str = "") -> Any:
        if not VNPY_AVAILABLE:
            return None
        name = (
            self._normalize_exchange(hint)
            or self._contract_map.get(symbol)
            or self._contract_map.get(symbol.lower())
            or self._contract_map.get(symbol.upper())
        )
        if not name:
            return None
        try:
            return Exchange(name)
        except Exception:
            return None

    @staticmethod
    def _parse_direction(d: str):
        if not VNPY_AVAILABLE:
            return None
        dl = d.lower()
        if dl in ("long", "buy", "多", "0"):
            return Direction.LONG
        return Direction.SHORT

    @staticmethod
    def _parse_offset(o: str):
        if not VNPY_AVAILABLE:
            return None
        ol = o.lower()
        if ol in ("open", "开", "0"):
            return Offset.OPEN
        return Offset.CLOSE

    @staticmethod
    def _build_setting(info: dict) -> dict:
        if not VNPY_AVAILABLE:
            return {}
        base = dict(getattr(CtpGateway, "default_setting", {}) or {})

        def _set(candidates, value):
            for k in candidates:
                if k in base:
                    base[k] = value
                    return
            if candidates:
                base[candidates[0]] = value

        _set(["用户名", "userid", "UserID"], info.get("userid", ""))
        _set(["密码", "password"], info.get("password", ""))
        _set(["经纪商代码", "brokerid", "BrokerID"], info.get("brokerid", ""))
        _set(["交易服务器", "td_address", "tdAddress"], info.get("td_address", ""))
        _set(["行情服务器", "md_address", "mdAddress"], info.get("md_address", ""))
        _set(["产品名称", "appid", "AppID"], info.get("appid", ""))
        _set(["授权编码", "auth_code", "AuthCode"], info.get("auth_code", ""))
        _set(["产品信息", "product_info"], info.get("product_info", ""))
        return base
