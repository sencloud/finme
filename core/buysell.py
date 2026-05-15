"""Buy/sell point detection (缠论买卖点判断).

Three types of buy/sell points:
  Type 1 (一买/一卖): Divergence at trend extremes.
  Type 2 (二买/二卖): Pullback after type-1 that doesn't break the extreme.
  Type 3 (三买/三卖): Pullback after leaving hub that doesn't re-enter hub.
Plus semi-type variants (类二买/类三买 etc.).
"""

from __future__ import annotations

from .types import BuySellType, ChanlunDirection, DivergenceType


def _calc_bi_macd_area(bi: dict, macd_data: dict | None) -> float:
    if not macd_data or not macd_data.get("histogram"):
        return 0.0
    start = bi["startFractal"]["klineIndex"]
    end = bi["endFractal"]["klineIndex"]
    area = 0.0
    hist = macd_data["histogram"]
    for i in range(start, min(end + 1, len(hist))):
        if hist[i] is not None:
            area += hist[i]
    return area


def _check_divergence(curr: dict, prev: dict, macd_data: dict | None) -> bool:
    has_macd = macd_data and macd_data.get("histogram") and len(macd_data["histogram"]) > 0

    if has_macd:
        curr_area = abs(_calc_bi_macd_area(curr, macd_data))
        prev_area = abs(_calc_bi_macd_area(prev, macd_data))
        return prev_area > 0 and curr_area < prev_area

    curr_range = abs(curr["high"] - curr["low"])
    prev_range = abs(prev["high"] - prev["low"])
    curr_len = curr["endFractal"]["index"] - curr["startFractal"]["index"]
    prev_len = prev["endFractal"]["index"] - prev["startFractal"]["index"]
    curr_strength = curr_range / max(curr_len, 1)
    prev_strength = prev_range / max(prev_len, 1)
    return curr_strength < prev_strength * 0.8


def _get_macd_detail(curr: dict, prev: dict, macd_data: dict | None) -> dict | None:
    has_macd = macd_data and macd_data.get("histogram") and len(macd_data["histogram"]) > 0
    if not has_macd:
        return None
    curr_area = abs(_calc_bi_macd_area(curr, macd_data))
    prev_area = abs(_calc_bi_macd_area(prev, macd_data))
    return {"currArea": round(curr_area, 2), "prevArea": round(prev_area, 2)}


def _find_away_segment(bis: list[dict], hub: dict, direction: str) -> dict | None:
    hub_bis = hub["bis"]
    hub_end_bi = hub_bis[-1]
    hub_end_bi_idx = -1
    for idx, b in enumerate(bis):
        if b is hub_end_bi:
            hub_end_bi_idx = idx
            break
    if hub_end_bi_idx < 0 or hub_end_bi_idx >= len(bis) - 1:
        return None

    for i in range(hub_end_bi_idx + 1, len(bis)):
        if bis[i]["direction"] == direction:
            has_left = False
            if direction == ChanlunDirection.DOWN and bis[i]["low"] < hub["ZD"]:
                has_left = True
            elif direction == ChanlunDirection.UP and bis[i]["high"] > hub["ZG"]:
                has_left = True
            if has_left:
                return bis[i]
    return None


def _find_entry_segment(bis: list[dict], hub: dict, direction: str) -> dict | None:
    hub_start_bi = hub["bis"][0]
    hub_start_bi_idx = -1
    for idx, b in enumerate(bis):
        if b is hub_start_bi:
            hub_start_bi_idx = idx
            break
    if hub_start_bi_idx <= 0:
        return None

    for i in range(hub_start_bi_idx - 1, -1, -1):
        if bis[i]["direction"] == direction:
            return bis[i]
    return None


def _find_type3_points(bis: list[dict], hubs: list[dict], points: list[dict]) -> None:
    for hub in hubs:
        hub_end_bi = hub["bis"][-1]
        hub_end_bi_idx = -1
        for idx, b in enumerate(bis):
            if b is hub_end_bi:
                hub_end_bi_idx = idx
                break
        if hub_end_bi_idx < 0:
            continue

        for j in range(hub_end_bi_idx + 1, len(bis)):
            bi = bis[j]
            if bi["direction"] == ChanlunDirection.DOWN and bi["low"] > hub["ZG"]:
                points.append({
                    "type": BuySellType.BUY3,
                    "divergenceType": None,
                    "price": bi["low"],
                    "date": bi["endFractal"]["date"],
                    "index": bi["endFractal"]["klineIndex"],
                    "description": f'三买：回踩不破中枢上沿 {hub["ZG"]:.2f}',
                    "macdDetail": None,
                })
            if bi["direction"] == ChanlunDirection.UP and bi["high"] < hub["ZD"]:
                points.append({
                    "type": BuySellType.SELL3,
                    "divergenceType": None,
                    "price": bi["high"],
                    "date": bi["endFractal"]["date"],
                    "index": bi["endFractal"]["klineIndex"],
                    "description": f'三卖：反弹不破中枢下沿 {hub["ZD"]:.2f}',
                    "macdDetail": None,
                })


def _find_trend_divergence(bis: list[dict], hubs: list[dict],
                           macd_data: dict | None, points: list[dict]) -> None:
    if len(hubs) < 2:
        return

    for h in range(1, len(hubs)):
        prev_hub = hubs[h - 1]
        curr_hub = hubs[h]

        if curr_hub["ZG"] < prev_hub["ZD"]:
            prev_away = _find_away_segment(bis, prev_hub, ChanlunDirection.DOWN)
            curr_away = _find_away_segment(bis, curr_hub, ChanlunDirection.DOWN)
            if prev_away and curr_away and curr_away["low"] < prev_away["low"]:
                if _check_divergence(curr_away, prev_away, macd_data):
                    points.append({
                        "type": BuySellType.BUY1,
                        "divergenceType": DivergenceType.TREND,
                        "price": curr_away["low"],
                        "date": curr_away["endFractal"]["date"],
                        "index": curr_away["endFractal"]["klineIndex"],
                        "description": "一买(趋势)：两中枢下跌趋势背驰",
                        "macdDetail": _get_macd_detail(curr_away, prev_away, macd_data),
                        "divergenceRegion": {
                            "currStart": curr_away["startFractal"]["klineIndex"],
                            "currEnd": curr_away["endFractal"]["klineIndex"],
                            "prevStart": prev_away["startFractal"]["klineIndex"],
                            "prevEnd": prev_away["endFractal"]["klineIndex"],
                        },
                    })

        if curr_hub["ZD"] > prev_hub["ZG"]:
            prev_away = _find_away_segment(bis, prev_hub, ChanlunDirection.UP)
            curr_away = _find_away_segment(bis, curr_hub, ChanlunDirection.UP)
            if prev_away and curr_away and curr_away["high"] > prev_away["high"]:
                if _check_divergence(curr_away, prev_away, macd_data):
                    points.append({
                        "type": BuySellType.SELL1,
                        "divergenceType": DivergenceType.TREND,
                        "price": curr_away["high"],
                        "date": curr_away["endFractal"]["date"],
                        "index": curr_away["endFractal"]["klineIndex"],
                        "description": "一卖(趋势)：两中枢上涨趋势背驰",
                        "macdDetail": _get_macd_detail(curr_away, prev_away, macd_data),
                        "divergenceRegion": {
                            "currStart": curr_away["startFractal"]["klineIndex"],
                            "currEnd": curr_away["endFractal"]["klineIndex"],
                            "prevStart": prev_away["startFractal"]["klineIndex"],
                            "prevEnd": prev_away["endFractal"]["klineIndex"],
                        },
                    })


def _find_consolidation_divergence(bis: list[dict], hubs: list[dict],
                                   macd_data: dict | None, points: list[dict],
                                   trend_hub_pairs: set[int]) -> None:
    for h in range(len(hubs)):
        hub = hubs[h]
        if h in trend_hub_pairs:
            continue

        entry_down = _find_entry_segment(bis, hub, ChanlunDirection.DOWN)
        leave_down = _find_away_segment(bis, hub, ChanlunDirection.DOWN)
        if entry_down and leave_down and leave_down["low"] < hub["ZD"]:
            if _check_divergence(leave_down, entry_down, macd_data):
                points.append({
                    "type": BuySellType.BUY1,
                    "divergenceType": DivergenceType.CONSOLIDATION,
                    "price": leave_down["low"],
                    "date": leave_down["endFractal"]["date"],
                    "index": leave_down["endFractal"]["klineIndex"],
                    "description": "一买(盘整)：盘整背驰，离开段力度减弱",
                    "macdDetail": _get_macd_detail(leave_down, entry_down, macd_data),
                    "divergenceRegion": {
                        "currStart": leave_down["startFractal"]["klineIndex"],
                        "currEnd": leave_down["endFractal"]["klineIndex"],
                        "prevStart": entry_down["startFractal"]["klineIndex"],
                        "prevEnd": entry_down["endFractal"]["klineIndex"],
                    },
                })

        entry_up = _find_entry_segment(bis, hub, ChanlunDirection.UP)
        leave_up = _find_away_segment(bis, hub, ChanlunDirection.UP)
        if entry_up and leave_up and leave_up["high"] > hub["ZG"]:
            if _check_divergence(leave_up, entry_up, macd_data):
                points.append({
                    "type": BuySellType.SELL1,
                    "divergenceType": DivergenceType.CONSOLIDATION,
                    "price": leave_up["high"],
                    "date": leave_up["endFractal"]["date"],
                    "index": leave_up["endFractal"]["klineIndex"],
                    "description": "一卖(盘整)：盘整背驰，离开段力度减弱",
                    "macdDetail": _get_macd_detail(leave_up, entry_up, macd_data),
                    "divergenceRegion": {
                        "currStart": leave_up["startFractal"]["klineIndex"],
                        "currEnd": leave_up["endFractal"]["klineIndex"],
                        "prevStart": entry_up["startFractal"]["klineIndex"],
                        "prevEnd": entry_up["endFractal"]["klineIndex"],
                    },
                })


def _find_type2_points(bis: list[dict], points: list[dict]) -> None:
    buy1sell1 = [p for p in points if p["type"] in (BuySellType.BUY1, BuySellType.SELL1)]

    for pt in buy1sell1:
        pt_bi_idx = -1
        for idx, b in enumerate(bis):
            if b["endFractal"]["klineIndex"] >= pt["index"]:
                pt_bi_idx = idx
                break
        if pt_bi_idx < 0:
            continue

        for offset in range(2, 5, 2):
            check_idx = pt_bi_idx + offset
            if check_idx >= len(bis):
                break
            pullback = bis[check_idx]

            if pt["type"] == BuySellType.BUY1 and pullback["direction"] == ChanlunDirection.DOWN:
                if pullback["low"] > pt["price"]:
                    points.append({
                        "type": BuySellType.BUY2,
                        "divergenceType": pt["divergenceType"],
                        "price": pullback["low"],
                        "date": pullback["endFractal"]["date"],
                        "index": pullback["endFractal"]["klineIndex"],
                        "description": f'二买：回踩不破一买低点 {pt["price"]:.2f}',
                        "macdDetail": None,
                    })
                    break

            if pt["type"] == BuySellType.SELL1 and pullback["direction"] == ChanlunDirection.UP:
                if pullback["high"] < pt["price"]:
                    points.append({
                        "type": BuySellType.SELL2,
                        "divergenceType": pt["divergenceType"],
                        "price": pullback["high"],
                        "date": pullback["endFractal"]["date"],
                        "index": pullback["endFractal"]["klineIndex"],
                        "description": f'二卖：反弹不破一卖高点 {pt["price"]:.2f}',
                        "macdDetail": None,
                    })
                    break


def _find_semi_type2_points(bis: list[dict], hubs: list[dict], points: list[dict]) -> None:
    for hub in hubs:
        for i, bi in enumerate(hub["bis"]):
            if bi["direction"] == ChanlunDirection.DOWN:
                prev_down = hub["bis"][i - 2] if i >= 2 else None
                if (prev_down and prev_down["direction"] == ChanlunDirection.DOWN
                        and bi["low"] > prev_down["low"] and bi["low"] >= hub["ZD"]):
                    points.append({
                        "type": BuySellType.SEMI_BUY2,
                        "divergenceType": None,
                        "price": bi["low"],
                        "date": bi["endFractal"]["date"],
                        "index": bi["endFractal"]["klineIndex"],
                        "description": f'类二买：中枢内低点抬高，不破 {prev_down["low"]:.2f}',
                        "macdDetail": None,
                    })

            if bi["direction"] == ChanlunDirection.UP:
                prev_up = hub["bis"][i - 2] if i >= 2 else None
                if (prev_up and prev_up["direction"] == ChanlunDirection.UP
                        and bi["high"] < prev_up["high"] and bi["high"] <= hub["ZG"]):
                    points.append({
                        "type": BuySellType.SEMI_SELL2,
                        "divergenceType": None,
                        "price": bi["high"],
                        "date": bi["endFractal"]["date"],
                        "index": bi["endFractal"]["klineIndex"],
                        "description": f'类二卖：中枢内高点降低，不破 {prev_up["high"]:.2f}',
                        "macdDetail": None,
                    })


def _find_semi_type3_points(bis: list[dict], hubs: list[dict], points: list[dict]) -> None:
    tolerance = 0.003

    for hub in hubs:
        hub_end_bi = hub["bis"][-1]
        hub_end_bi_idx = -1
        for idx, b in enumerate(bis):
            if b is hub_end_bi:
                hub_end_bi_idx = idx
                break
        if hub_end_bi_idx < 0:
            continue

        for j in range(hub_end_bi_idx + 1, len(bis)):
            bi = bis[j]
            if bi["direction"] == ChanlunDirection.DOWN:
                threshold = hub["ZG"] * (1 - tolerance)
                if bi["low"] >= threshold and bi["low"] <= hub["ZG"] * (1 + tolerance):
                    points.append({
                        "type": BuySellType.SEMI_BUY3,
                        "divergenceType": None,
                        "price": bi["low"],
                        "date": bi["endFractal"]["date"],
                        "index": bi["endFractal"]["klineIndex"],
                        "description": f'类三买：回踩触碰中枢上沿 {hub["ZG"]:.2f} 附近',
                        "macdDetail": None,
                    })

            if bi["direction"] == ChanlunDirection.UP:
                threshold = hub["ZD"] * (1 + tolerance)
                if bi["high"] <= threshold and bi["high"] >= hub["ZD"] * (1 - tolerance):
                    points.append({
                        "type": BuySellType.SEMI_SELL3,
                        "divergenceType": None,
                        "price": bi["high"],
                        "date": bi["endFractal"]["date"],
                        "index": bi["endFractal"]["klineIndex"],
                        "description": f'类三卖：反弹触碰中枢下沿 {hub["ZD"]:.2f} 附近',
                        "macdDetail": None,
                    })


def _deduplicate_and_sort(points: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for p in points:
        key = f'{p["type"]}_{p["index"]}'
        if key not in seen:
            seen.add(key)
            result.append(p)
    result.sort(key=lambda p: p["index"])
    return result


def find_buysell_points(bis: list[dict], hubs: list[dict],
                        merged_klines: list[dict],
                        macd_data: dict | None = None,
                        config: dict | None = None) -> list[dict]:
    """Identify all buy/sell points from bi and hub data."""
    if not bis or len(bis) < 5:
        return []

    points: list[dict] = []

    if hubs:
        _find_type3_points(bis, hubs, points)

    trend_hub_pairs: set[int] = set()
    if hubs and len(hubs) >= 2:
        for h in range(1, len(hubs)):
            prev_hub = hubs[h - 1]
            curr_hub = hubs[h]
            if curr_hub["ZG"] < prev_hub["ZD"] or curr_hub["ZD"] > prev_hub["ZG"]:
                trend_hub_pairs.add(h - 1)
                trend_hub_pairs.add(h)
        _find_trend_divergence(bis, hubs, macd_data, points)

    if hubs and len(hubs) >= 1:
        _find_consolidation_divergence(bis, hubs, macd_data, points, trend_hub_pairs)

    _find_type2_points(bis, points)

    if hubs:
        _find_semi_type2_points(bis, hubs, points)
        _find_semi_type3_points(bis, hubs, points)

    result = _deduplicate_and_sort(points)
    _attach_bi_finished(result, bis)
    return result


def _attach_bi_finished(points: list[dict], bis: list[dict]) -> None:
    """Tag each point with the finished status of its controlling bi.

    A point is structurally confirmed only when the bi it sits on has
    ``finished == True`` (i.e. a subsequent opposing fractal exists).
    Points on the last virtual/unfinished bi are provisional.
    """
    bi_end_index_map: dict[int, bool] = {}
    for bi in bis:
        ef = bi.get("endFractal")
        if ef:
            bi_end_index_map[ef.get("klineIndex", -1)] = bi.get("finished", True)

    last_finished_bi_end = -1
    for bi in bis:
        if bi.get("finished", True):
            ef = bi.get("endFractal")
            if ef:
                idx = ef.get("klineIndex", -1)
                if idx > last_finished_bi_end:
                    last_finished_bi_end = idx

    for pt in points:
        pt_idx = pt.get("index", 0)
        finished = bi_end_index_map.get(pt_idx)
        if finished is not None:
            pt["biFinished"] = finished
        else:
            pt["biFinished"] = pt_idx <= last_finished_bi_end
