"""Fractal identification (缠论分型识别).

Top fractal: middle bar's high > both neighbors' highs.
Bottom fractal: middle bar's low < both neighbors' lows.
"""

from __future__ import annotations

from .types import FractalType


def find_fractals(merged_klines: list[dict]) -> list[dict]:
    """Identify all fractals in a merged K-line sequence."""
    if not merged_klines or len(merged_klines) < 3:
        return []

    fractals: list[dict] = []

    for i in range(1, len(merged_klines) - 1):
        prev = merged_klines[i - 1]
        curr = merged_klines[i]
        nxt = merged_klines[i + 1]

        if curr["high"] > prev["high"] and curr["high"] > nxt["high"]:
            fractals.append({
                "type": FractalType.TOP,
                "index": i,
                "high": curr["high"],
                "low": curr["low"],
                "date": curr["date"],
                "klineIndex": curr["endIndex"],
            })

        if curr["low"] < prev["low"] and curr["low"] < nxt["low"]:
            fractals.append({
                "type": FractalType.BOTTOM,
                "index": i,
                "high": curr["high"],
                "low": curr["low"],
                "date": curr["date"],
                "klineIndex": curr["startIndex"],
            })

    return fractals
