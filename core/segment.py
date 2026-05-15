"""Segment construction from bi sequence (缠论线段构建).

A segment consists of at least 3 bi in the same direction.

Type-1 termination (characteristic sequence fractal method):
  Extract opposite-direction bi as characteristic sequence, merge inclusions,
  then find fractal to confirm segment end.

Type-2 termination (bi-break method):
  A bi directly breaks the segment starting price.
"""

from __future__ import annotations

from .types import ChanlunDirection, SegmentEndType


def _merge_char_sequence(elements: list[dict], seg_direction: str) -> list[dict]:
    """Merge inclusion relations in characteristic sequence."""
    if len(elements) < 2:
        return [dict(e) for e in elements]

    merged: list[dict] = []
    for el in elements:
        if not merged:
            merged.append(dict(el))
            continue

        prev = merged[-1]
        has_incl = (prev["high"] >= el["high"] and prev["low"] <= el["low"]) or \
                   (el["high"] >= prev["high"] and el["low"] <= prev["low"])

        if has_incl:
            if seg_direction == ChanlunDirection.UP:
                prev["high"] = max(prev["high"], el["high"])
                prev["low"] = max(prev["low"], el["low"])
            else:
                prev["high"] = min(prev["high"], el["high"])
                prev["low"] = min(prev["low"], el["low"])
            prev["endBiIndex"] = el["biIndex"]
        else:
            merged.append(dict(el))

    return merged


def _find_segment_end(bis: list[dict], start_idx: int, seg_direction: str) -> int:
    """Type-1 termination: find segment end via characteristic sequence fractal."""
    char_seq: list[dict] = []
    for i in range(start_idx + 1, len(bis)):
        if bis[i]["direction"] != seg_direction:
            char_seq.append({
                "high": bis[i]["high"],
                "low": bis[i]["low"],
                "biIndex": i,
                "endBiIndex": i,
            })

    if len(char_seq) < 3:
        return -1

    merged = _merge_char_sequence(char_seq, seg_direction)
    if len(merged) < 3:
        return -1

    for i in range(1, len(merged) - 1):
        prev_m = merged[i - 1]
        curr_m = merged[i]
        next_m = merged[i + 1]
        fractal_found = False

        if seg_direction == ChanlunDirection.UP:
            fractal_found = curr_m["high"] > prev_m["high"] and curr_m["high"] > next_m["high"]
        else:
            fractal_found = curr_m["low"] < prev_m["low"] and curr_m["low"] < next_m["low"]

        if fractal_found:
            end_bi = curr_m["biIndex"] - 1
            if end_bi >= start_idx and end_bi - start_idx + 1 >= 3:
                return end_bi

    return -1


def _find_segment_end_type2(bis: list[dict], start_idx: int, seg_direction: str) -> int:
    """Type-2 termination: a bi directly breaks the segment start price."""
    start_bi = bis[start_idx]
    start_price = start_bi["low"] if seg_direction == ChanlunDirection.UP else start_bi["high"]

    for i in range(start_idx + 2, len(bis)):
        bi = bis[i]
        if seg_direction == ChanlunDirection.UP and bi["direction"] == ChanlunDirection.DOWN:
            if bi["low"] < start_price:
                end_bi = i - 1
                if end_bi - start_idx + 1 >= 3:
                    return end_bi

        if seg_direction == ChanlunDirection.DOWN and bi["direction"] == ChanlunDirection.UP:
            if bi["high"] > start_price:
                end_bi = i - 1
                if end_bi - start_idx + 1 >= 3:
                    return end_bi

    return -1


def build_segments(bis: list[dict], config: dict | None = None) -> list[dict]:
    """Build segments from bi sequence."""
    if not bis or len(bis) < 3:
        return []

    cfg = config or {}
    use_type2 = cfg.get("segEndMode") != "type1Only"
    segments: list[dict] = []
    seg_start = 0

    while seg_start < len(bis) - 2:
        seg_direction = bis[seg_start]["direction"]
        end_idx1 = _find_segment_end(bis, seg_start, seg_direction)

        end_idx = end_idx1
        end_type = SegmentEndType.CHAR_SEQUENCE

        if use_type2:
            end_idx2 = _find_segment_end_type2(bis, seg_start, seg_direction)
            if end_idx2 > seg_start:
                if end_idx1 > seg_start:
                    if end_idx2 < end_idx1:
                        end_idx = end_idx2
                        end_type = SegmentEndType.BI_BREAK
                else:
                    end_idx = end_idx2
                    end_type = SegmentEndType.BI_BREAK

        if end_idx > seg_start:
            seg_bis = bis[seg_start:end_idx + 1]
            high = max(b["high"] for b in seg_bis)
            low = min(b["low"] for b in seg_bis)

            segments.append({
                "startBi": bis[seg_start],
                "endBi": bis[end_idx],
                "direction": seg_direction,
                "high": high,
                "low": low,
                "bis": seg_bis,
                "endType": end_type,
            })
            seg_start = end_idx + 1
        else:
            if len(bis) - seg_start >= 3:
                seg_bis = bis[seg_start:]
                high = max(b["high"] for b in seg_bis)
                low = min(b["low"] for b in seg_bis)
                segments.append({
                    "startBi": bis[seg_start],
                    "endBi": bis[-1],
                    "direction": seg_direction,
                    "high": high,
                    "low": low,
                    "bis": seg_bis,
                    "endType": SegmentEndType.CHAR_SEQUENCE,
                })
            break

    return segments
