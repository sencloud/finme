"""JSON-backed persistence for ``LivePosition`` objects + variety states.

Atomic writes (write to ``.tmp``, then ``os.replace``) ensure we never leave
a half-written file behind. The schema is intentionally simple and
forward-compatible (a top-level ``version`` field guards against breaking
changes; older readers gracefully degrade by ignoring unknown keys).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Mapping

from .live_position import LivePosition

logger = logging.getLogger(__name__)

_STORE_VERSION = 1


class PositionStore:
    """Persist ``{prefix: LivePosition}`` plus per-variety scan state.

    The store is intentionally write-through: callers invoke :py:meth:`save`
    after every state-changing scan; loads happen exactly once at startup.
    """

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Return ``{"positions": {...LivePosition}, "varietyState": {...}, "consumedSignals": {...}}``.

        Any IO / decode error is logged and converted to an empty payload so
        the engine can still start with a clean slate.
        """
        empty = {"positions": {}, "varietyState": {}, "consumedSignals": {}}
        if not self.path.exists():
            return empty

        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("[PositionStore] 读取持仓快照失败 %s: %s", self.path, exc)
            return empty

        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            logger.error("[PositionStore] 持仓快照JSON解析失败 %s: %s — 以空状态启动",
                         self.path, exc)
            return empty

        positions: dict[str, LivePosition] = {}
        for key, payload in (data.get("positions") or {}).items():
            try:
                positions[key] = LivePosition.from_dict(payload)
            except Exception as exc:
                logger.error("[PositionStore] 反序列化 %s 失败: %s — 跳过", key, exc)

        consumed: dict[str, list[int]] = {}
        for prefix, stamps in (data.get("consumedSignals") or {}).items():
            try:
                consumed[prefix] = [int(s) for s in (stamps or []) if s]
            except (TypeError, ValueError):
                continue

        if positions:
            logger.info(
                "[PositionStore] 已从 %s 加载 %d 个持仓: %s",
                self.path, len(positions), ", ".join(sorted(positions.keys())),
            )

        return {
            "positions": positions,
            "varietyState": dict(data.get("varietyState") or {}),
            "consumedSignals": consumed,
        }

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self,
             positions: Mapping[str, LivePosition],
             variety_state: Mapping[str, dict] | None = None,
             consumed_signals: Mapping[str, "list[int] | set[int]"] | None = None) -> None:
        payload = {
            "version": _STORE_VERSION,
            "positions": {k: pos.to_dict() for k, pos in positions.items()},
            "varietyState": dict(variety_state or {}),
            "consumedSignals": {
                str(k): sorted(int(s) for s in v)
                for k, v in (consumed_signals or {}).items()
                if v
            },
        }

        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.error("[PositionStore] 写入持仓快照失败 %s: %s", self.path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
