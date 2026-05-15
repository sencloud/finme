"""Persistent queue of orders deferred until the next trading session.

When ``AutoOrderManager`` tries to open/close a position while the market
is closed, CTP rejects the ``send_order`` with error code ``-1`` and the
engine would otherwise spam identical (useless) requests every scan. We
instead drop the intended order into a :class:`PendingOrderQueue`, persist
it, and flush it at the next ``flush()`` call that happens while the
market is open.

The queue is intentionally small and defensive:

* De-duplicated by ``(symbol, direction, offset)`` — only the latest
  request for any given slot is kept. This prevents the "attempt every
  15 min" scan from piling up 40 identical stale SR shorts.
* Each record carries an ``expires_at`` (UTC-naive ISO string). Once the
  deadline passes the item is dropped silently — so yesterday's stale
  intent can never trigger at tomorrow's open.
* Persistence is atomic (write tmp + rename). Any IO error is logged and
  the queue keeps operating in memory.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL_HOURS = 18  # cover a full overnight gap but expire stale intents


class PendingOrderQueue:
    """Small JSON-backed FIFO of queued order intents."""

    def __init__(self, path: str | os.PathLike,
                 ttl_hours: float = _DEFAULT_TTL_HOURS) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=float(ttl_hours))
        self._items: list[dict] = []
        self._load()

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def items(self) -> list[dict]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def enqueue(self, order: dict) -> dict:
        """Append (or replace) a pending order request.

        ``order`` must include: symbol, direction ("long"/"short"), offset
        ("open"/"close"), price (float), volume (int), exchange (str),
        variety_code (str). Any other field is preserved verbatim.
        """
        item = dict(order)
        item.setdefault("queued_at", datetime.now().isoformat(timespec="seconds"))
        item.setdefault("expires_at",
                        (datetime.now() + self.ttl).isoformat(timespec="seconds"))
        item.setdefault("attempts", 0)

        slot = self._slot_key(item)
        self._items = [it for it in self._items if self._slot_key(it) != slot]
        self._items.append(item)

        logger.info(
            "[PendingOrders] 入队 %s %s %s @ %s x%s (队列=%d)",
            item.get("offset"), item.get("direction"), item.get("symbol"),
            item.get("price"), item.get("volume"), len(self._items),
        )
        self._persist()
        return item

    def remove(self, order: dict) -> None:
        slot = self._slot_key(order)
        before = len(self._items)
        self._items = [it for it in self._items if self._slot_key(it) != slot]
        if before != len(self._items):
            self._persist()

    def clear(self) -> None:
        if self._items:
            self._items.clear()
            self._persist()

    # ------------------------------------------------------------------
    # Flush — called periodically to dispatch ripe items
    # ------------------------------------------------------------------

    def flush(self,
              is_tradeable: Callable[[str], bool],
              dispatch: Callable[[dict], dict],
              now: Optional[datetime] = None) -> list[dict]:
        """Attempt to send each queued item whose market is now open.

        Parameters
        ----------
        is_tradeable:
            ``is_tradeable(variety_prefix) -> bool`` — true when the given
            product is inside an active CTP trading session.
        dispatch:
            ``dispatch(order_dict) -> result_dict`` — actually places the
            order via CTP and returns the standard status dict. If the
            returned status is ``queued``/``ctp_not_ready``/``failed`` we
            leave the item in the queue for another retry; on any other
            status the item is consumed.

        Returns the list of dispatch results (for logging / audit).
        """
        if now is None:
            now = datetime.now()

        results: list[dict] = []
        kept: list[dict] = []
        changed = False

        for item in self._items:
            try:
                expiry = datetime.fromisoformat(item.get("expires_at", ""))
            except ValueError:
                expiry = now + self.ttl
            if expiry <= now:
                logger.warning(
                    "[PendingOrders] 丢弃过期待发单: %s %s %s @ %s",
                    item.get("offset"), item.get("direction"),
                    item.get("symbol"), item.get("price"),
                )
                results.append({
                    "status": "expired",
                    "order": item,
                    "reason": "超过TTL未被派发",
                })
                changed = True
                continue

            variety = item.get("variety_code") or ""
            if not is_tradeable(variety):
                kept.append(item)
                continue

            item["attempts"] = int(item.get("attempts") or 0) + 1
            try:
                dispatch_result = dispatch(item) or {}
            except Exception as exc:
                logger.exception("[PendingOrders] 派发异常: %s", exc)
                dispatch_result = {"status": "failed", "reason": str(exc)}

            results.append({"order": item, **dispatch_result})
            status = str(dispatch_result.get("status") or "")
            if status in ("queued", "ctp_not_ready", "failed"):
                # Keep for the next flush; update stored attempts.
                kept.append(item)
                changed = True
            else:
                changed = True  # item consumed

        self._items = kept
        if changed:
            self._persist()
        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8") or ""
            data = json.loads(raw) if raw.strip() else {}
        except Exception as exc:
            logger.error("[PendingOrders] 读取队列失败 %s: %s", self.path, exc)
            return
        items = data.get("items") or []
        if isinstance(items, list):
            self._items = [it for it in items if isinstance(it, dict)]
            if self._items:
                logger.info("[PendingOrders] 已从 %s 加载 %d 条待发单",
                            self.path, len(self._items))

    def _persist(self) -> None:
        payload = {"version": 1, "items": self._items}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.error("[PendingOrders] 写入队列失败 %s: %s", self.path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _slot_key(item: dict) -> tuple[str, str, str]:
        return (
            str(item.get("symbol") or "").upper(),
            str(item.get("direction") or "").lower(),
            str(item.get("offset") or "").lower(),
        )
