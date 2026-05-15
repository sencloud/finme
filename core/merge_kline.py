"""K-line inclusion-relation merge (缠论K线合并).

Rules:
  1. Two adjacent K-lines with inclusion relation must be merged.
  2. Merge direction depends on current trend:
     - Up trend: take higher high and higher low
     - Down trend: take lower high and lower low
  3. Initial direction is determined by the first two non-inclusive K-lines.
"""

from __future__ import annotations

from .types import ChanlunDirection


def has_inclusion(k1: dict, k2: dict) -> bool:
    return (k1["high"] >= k2["high"] and k1["low"] <= k2["low"]) or \
           (k2["high"] >= k1["high"] and k2["low"] <= k1["low"])


def merge_klines(klines: list[dict]) -> list[dict]:
    """Merge raw K-lines by inclusion relation.

    Each input bar: {high, low, open, close, date, time, volume, ...}
    Returns merged bars with added startIndex/endIndex/mergeCount/direction.
    """
    if not klines or len(klines) < 3:
        return []

    merged: list[dict] = []
    direction = ChanlunDirection.NONE

    for i, bar in enumerate(klines):
        mk = {
            "startIndex": i,
            "endIndex": i,
            "high": bar["high"],
            "low": bar["low"],
            "open": bar["open"],
            "close": bar["close"],
            "date": bar.get("date", ""),
            "time": bar.get("time", 0),
            "mergeCount": 1,
            "direction": ChanlunDirection.NONE,
        }

        if not merged:
            merged.append(mk)
            continue

        prev = merged[-1]

        if has_inclusion(prev, mk):
            if direction == ChanlunDirection.NONE and len(merged) >= 2:
                pp = merged[-2]
                direction = ChanlunDirection.UP if prev["high"] > pp["high"] else ChanlunDirection.DOWN

            if direction == ChanlunDirection.UP:
                prev["high"] = max(prev["high"], mk["high"])
                prev["low"] = max(prev["low"], mk["low"])
            else:
                prev["high"] = min(prev["high"], mk["high"])
                prev["low"] = min(prev["low"], mk["low"])

            prev["endIndex"] = i
            prev["mergeCount"] += 1
            prev["close"] = bar["close"]
            prev["date"] = bar.get("date", "")
            prev["time"] = bar.get("time", 0)
        else:
            if mk["high"] > prev["high"]:
                direction = ChanlunDirection.UP
            elif mk["low"] < prev["low"]:
                direction = ChanlunDirection.DOWN
            mk["direction"] = direction
            merged.append(mk)

    return merged
