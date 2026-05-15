"""Bi (stroke) construction from fractals (缠论笔构建).

Rules:
  1. Top fractal must follow bottom fractal and vice versa.
  2. New-bi mode: >=4 merged K-lines between fractals; old-bi: >=3.
  3. Up bi: bottom -> top; Down bi: top -> bottom.
  4. Strict top-bottom relationship required.
  5. Last bi may be unfinished (no confirming fractal yet).
"""

from __future__ import annotations

import math

from .types import ChanlunDirection, FractalType


def build_bis(fractals: list[dict], merged_klines: list[dict],
              config: dict | None = None) -> list[dict]:
    """Build bi (strokes) from fractal sequence."""
    if not fractals or len(fractals) < 2:
        return []

    cfg = config or {}
    min_distance = 3 if cfg.get("biMode") == "old" else 4
    bis: list[dict] = []
    last_fractal: dict | None = None

    for f in fractals:
        if last_fractal is None:
            last_fractal = f
            continue

        if f["type"] == last_fractal["type"]:
            if f["type"] == FractalType.TOP and f["high"] > last_fractal["high"]:
                last_fractal = f
            elif f["type"] == FractalType.BOTTOM and f["low"] < last_fractal["low"]:
                last_fractal = f
            continue

        distance = f["index"] - last_fractal["index"]
        if distance < min_distance:
            if f["type"] == FractalType.TOP and f["high"] > last_fractal["high"]:
                last_fractal = f
            elif f["type"] == FractalType.BOTTOM and f["low"] < last_fractal["low"]:
                last_fractal = f
            continue

        is_valid = False
        if last_fractal["type"] == FractalType.BOTTOM and f["type"] == FractalType.TOP:
            is_valid = f["high"] > last_fractal["high"]
        elif last_fractal["type"] == FractalType.TOP and f["type"] == FractalType.BOTTOM:
            is_valid = f["low"] < last_fractal["low"]

        if not is_valid:
            if f["type"] == FractalType.TOP and f["high"] > last_fractal["high"]:
                last_fractal = f
            elif f["type"] == FractalType.BOTTOM and f["low"] < last_fractal["low"]:
                last_fractal = f
            continue

        direction = ChanlunDirection.UP if last_fractal["type"] == FractalType.BOTTOM else ChanlunDirection.DOWN
        high = f["high"] if direction == ChanlunDirection.UP else last_fractal["high"]
        low = last_fractal["low"] if direction == ChanlunDirection.UP else f["low"]

        bis.append({
            "startFractal": last_fractal,
            "endFractal": f,
            "direction": direction,
            "high": high,
            "low": low,
            "finished": True,
        })
        last_fractal = f

    # Virtual unfinished bi
    if bis and merged_klines and len(merged_klines) > 0:
        last_bi = bis[-1]
        end_fractal = last_bi["endFractal"]
        last_merged_idx = len(merged_klines) - 1

        if last_merged_idx > end_fractal["index"] + 1:
            next_dir = ChanlunDirection.DOWN if last_bi["direction"] == ChanlunDirection.UP else ChanlunDirection.UP
            extreme_val = -math.inf if next_dir == ChanlunDirection.UP else math.inf
            extreme_idx = None

            for k in range(end_fractal["index"] + 1, last_merged_idx + 1):
                mk = merged_klines[k]
                if next_dir == ChanlunDirection.UP and mk["high"] > extreme_val:
                    extreme_val = mk["high"]
                    extreme_idx = k
                elif next_dir == ChanlunDirection.DOWN and mk["low"] < extreme_val:
                    extreme_val = mk["low"]
                    extreme_idx = k

            if extreme_idx is not None and extreme_idx > end_fractal["index"] + 1:
                mk = merged_klines[extreme_idx]
                virtual_fractal = {
                    "type": FractalType.TOP if next_dir == ChanlunDirection.UP else FractalType.BOTTOM,
                    "index": extreme_idx,
                    "high": mk["high"],
                    "low": mk["low"],
                    "date": mk["date"],
                    "klineIndex": mk["endIndex"] if next_dir == ChanlunDirection.UP else mk["startIndex"],
                }
                bis.append({
                    "startFractal": end_fractal,
                    "endFractal": virtual_fractal,
                    "direction": next_dir,
                    "high": virtual_fractal["high"] if next_dir == ChanlunDirection.UP else end_fractal["high"],
                    "low": end_fractal["low"] if next_dir == ChanlunDirection.UP else virtual_fractal["low"],
                    "finished": False,
                })

    return bis
