"""Signal rule engine for futures trading.

Ported from main/services/signal-rules.js.
Applies Chanlun buy/sell point filtering, computes entry/stop-loss/take-profit
via ATR, scores signals with combo strategy.
"""

from __future__ import annotations

from datetime import datetime

from . import chanlun_combo as combo

DEFAULT_CONFIG = {
    "recentBars": 5,
    "requireFinished": True,
    "requireTrendAlignment": False,
    "rolloverToleranceDays": 5,
    "validSignalTypes": ["buy1", "buy2", "buy3", "sell1", "sell2", "sell3"],
    "includePartialTypes": False,
    "stopLossATRMultiple": 2.0,
    "takeProfitATRMultiple": 3.0,
    "comboMinScore": 64,
    "comboShadowAtrMultiplier": 0.75,
    "comboShadowBodyRatio": 1.8,
    "comboHourlyZoneATR": 0.8,
}


def apply_signal_rules(raw: dict, config: dict | None = None) -> list[dict]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    entry_result = raw.get("entryResult")
    signals: list[dict] = []

    if entry_result:
        structure_result = raw.get("structureResult")
        trend_result = raw.get("trendResult")
        structure_dir = structure_result["currentTrend"] if structure_result else None
        trend_dir = trend_result["currentTrend"] if trend_result else structure_dir

        entry_signals = _filter_points(
            points=entry_result.get("buySellPoints", []),
            bis=entry_result.get("bis", []),
            hubs=entry_result.get("hubs", []),
            klines=entry_result.get("mergedKlines", []),
            cfg=cfg,
            meta={
                "varietyCode": raw.get("prefix", ""),
                "displayName": raw.get("displayName", ""),
                "trendSeriesCode": raw.get("trendCode", ""),
                "executionTsCode": raw.get("executionCode", ""),
                "timeframe": "15m",
                "seriesType": "execution",
                "trendDirection": trend_dir,
                "movementType": entry_result.get("movementType"),
                "mappingDate": raw.get("mappingDate"),
                "structureDirection": structure_dir,
            },
            parent_trend_dir=structure_dir or trend_dir,
        )
        signals.extend(_decorate_combo_signal(s, raw, cfg) for s in entry_signals)
        signals.extend(_build_shadow_signals(raw, cfg))

    result = deduplicate_signals(signals)

    min_score = cfg.get("comboMinScore", 0)
    if min_score > 0:
        result = [s for s in result if s.get("compositeScore", 0) >= min_score]

    for sig in result:
        _tag_tradability(sig)

    return result


def _decorate_combo_signal(signal: dict, raw: dict, cfg: dict) -> dict:
    trend_result = raw.get("trendResult")
    structure_result = raw.get("structureResult")
    entry_result = raw.get("entryResult")
    direction = signal["direction"]
    phase = combo.get_corn_season_phase(signal.get("date"))
    season = combo.get_seasonal_alignment(direction, signal.get("date"))
    time_ctx = combo.get_time_context(signal.get("date"))
    structure_point = _find_latest_structure_point(structure_result, signal.get("date"), direction)
    active_hub = _find_recent_hub(structure_result or entry_result, signal.get("date"))

    score = 45
    score += _score_trend_alignment(trend_result["currentTrend"] if trend_result else None, direction, 18, -12)
    score += _score_trend_alignment(structure_result["currentTrend"] if structure_result else None, direction, 12, -8)
    score += _score_signal_type(signal["type"], True)
    score += season.get("score", 0)
    score += time_ctx.get("score", 0)

    if structure_point:
        score += round(_score_signal_type(structure_point["type"], False) * 0.7)
    rr = signal.get("riskRewardRatio", 0)
    if rr >= 2:
        score += 6
    elif rr < 1.2:
        score -= 6
    if signal.get("finished"):
        score += 4
    if active_hub:
        klines = (entry_result or {}).get("mergedKlines", [])
        if _is_near_hub_edge(signal.get("entryPrice", 0), active_hub, direction, klines[-14:], cfg):
            score += 8

    signal["signalFamily"] = combo.get_signal_family(signal["type"])
    signal["compositeScore"] = score
    signal["seasonPhase"] = phase["name"]
    signal["seasonCode"] = phase["code"]
    signal["timeSegment"] = time_ctx["segment"]
    trend_label_t = _trend_label(trend_result.get("currentTrend") if trend_result else None)
    trend_label_s = _trend_label(structure_result.get("currentTrend") if structure_result else None)
    signal["trendContext"] = f"{trend_label_t} / {trend_label_s}"
    signal["structureSignal"] = combo.get_signal_label(structure_point["type"]) if structure_point else None
    signal["activeHub"] = {"ZD": active_hub["ZD"], "ZG": active_hub["ZG"]} if active_hub else None
    signal["confidence"] = "high" if score >= 82 else ("medium" if score >= cfg["comboMinScore"] else "low")
    signal["reasons"] = [
        f"组合评分 {score}",
        f"季节阶段: {phase['name']}",
        f"时段: {time_ctx['segment']}",
        *(signal.get("reasons") or []),
    ]
    return signal


def _build_shadow_signals(raw: dict, cfg: dict) -> list[dict]:
    entry_result = raw.get("entryResult")
    if not entry_result:
        return []
    klines = entry_result.get("mergedKlines", [])
    if len(klines) < 5:
        return []

    recent_bars = klines[-max(cfg["recentBars"], 3):]
    atr = compute_atr(klines, 14)
    results: list[dict] = []

    for bar in recent_bars:
        shadow = combo.detect_shadow_signal(bar, atr, {
            "shadowAtrMultiplier": cfg["comboShadowAtrMultiplier"],
            "shadowBodyRatio": cfg["comboShadowBodyRatio"],
        })

        if shadow["longLower"]:
            sig = _build_shadow_signal(
                sig_type="shadowLong", direction="long", label="长下影做多",
                bar=bar, raw=raw, cfg=cfg,
            )
            if sig:
                results.append(sig)

        if shadow["longUpper"]:
            sig = _build_shadow_signal(
                sig_type="shadowShort", direction="short", label="长上影做空",
                bar=bar, raw=raw, cfg=cfg,
            )
            if sig:
                results.append(sig)

    return results


def _build_shadow_signal(*, sig_type: str, direction: str, label: str,
                         bar: dict, raw: dict, cfg: dict) -> dict | None:
    trend_result = raw.get("trendResult")
    structure_result = raw.get("structureResult")
    entry_result = raw.get("entryResult")
    phase = combo.get_corn_season_phase(bar.get("date"))
    season = combo.get_seasonal_alignment(direction, bar.get("date"))
    time_ctx = combo.get_time_context(bar.get("date"))
    active_hub = _find_recent_hub(structure_result or entry_result, bar.get("date"))

    if not active_hub:
        return None
    if not _is_near_hub_edge(bar.get("close", 0), active_hub, direction,
                             entry_result.get("mergedKlines", []), cfg):
        return None

    entry = _compute_entry_details(
        {"price": bar.get("close", 0)},
        entry_result.get("bis", []),
        entry_result.get("hubs", []),
        entry_result.get("mergedKlines", []),
        cfg, direction,
    )

    score = 48
    score += _score_trend_alignment(trend_result["currentTrend"] if trend_result else None, direction, 18, -12)
    score += _score_trend_alignment(structure_result["currentTrend"] if structure_result else None, direction, 12, -8)
    score += season.get("score", 0)
    score += time_ctx.get("score", 0)
    score += 14
    if entry["riskRewardRatio"] >= 2:
        score += 6

    prefix = raw.get("prefix", "")
    trend_code = raw.get("trendCode", "")
    execution_code = raw.get("executionCode", "")
    display_name = raw.get("displayName", "")
    mapping_date = raw.get("mappingDate")

    return {
        "id": build_dedupe_key(prefix, execution_code or trend_code, "15m", sig_type, bar.get("date", "")),
        "varietyCode": prefix,
        "displayName": display_name,
        "trendSeriesCode": trend_code,
        "executionTsCode": execution_code,
        "type": sig_type,
        "direction": direction,
        "price": bar.get("close", 0),
        "entryPrice": entry["entryPrice"],
        "stopLoss": entry["stopLoss"],
        "takeProfit": entry["takeProfit"],
        "riskRewardRatio": entry["riskRewardRatio"],
        "date": bar.get("date", ""),
        "timeframe": "15m",
        "seriesType": "execution",
        "finished": True,
        "trendDirection": trend_result["currentTrend"] if trend_result else None,
        "trendAligned": _score_trend_alignment(
            trend_result["currentTrend"] if trend_result else None, direction, 1, -1) > 0,
        "seasonalAlignment": season.get("aligned"),
        "confidence": "high" if score >= 82 else ("medium" if score >= cfg["comboMinScore"] else "low"),
        "compositeScore": score,
        "signalFamily": "长下影" if direction == "long" else "长上影",
        "trendContext": f'{_trend_label(trend_result.get("currentTrend") if trend_result else None)}'
                        f' / {_trend_label(structure_result.get("currentTrend") if structure_result else None)}',
        "seasonPhase": phase["name"],
        "seasonCode": phase["code"],
        "timeSegment": time_ctx["segment"],
        "reasons": [
            f"{display_name} 15分钟{label}",
            f'1小时靠近中枢{"下沿" if direction == "long" else "上沿"}',
            f"季节阶段: {phase['name']}",
            f"组合评分 {score}",
        ],
        "divergenceType": None,
        "mappingDate": mapping_date,
        "confirmed": True,
        "source": "chanlun",
        "status": "pending",
        "createdAt": datetime.now().isoformat(),
    }


# ===== Filtering =====

def _filter_points(*, points, bis, hubs, klines, cfg, meta, parent_trend_dir) -> list[dict]:
    bi_count = len(bis)
    result: list[dict] = []

    for pt in points:
        valid_types = cfg["validSignalTypes"]
        if pt["type"] not in valid_types and not cfg["includePartialTypes"]:
            if not str(pt.get("type", "")).startswith("semi"):
                continue
            if not cfg["includePartialTypes"]:
                continue
        if pt["type"] not in valid_types and not cfg["includePartialTypes"]:
            continue

        bi_idx = _find_bi_index(pt, bis)
        if 0 <= bi_idx and bi_count - bi_idx > cfg["recentBars"]:
            continue

        trend_aligned = None
        if parent_trend_dir and cfg["requireTrendAlignment"]:
            trend_aligned = _check_alignment(parent_trend_dir, pt["type"])
            if not trend_aligned:
                continue
        elif parent_trend_dir:
            trend_aligned = _check_alignment(parent_trend_dir, pt["type"])

        is_buy = "buy" in pt["type"].lower()
        direction = "long" if is_buy else "short"

        entry = _compute_entry_details(pt, bis, hubs, klines, cfg, direction)
        reasons = _build_reasons(pt, meta, trend_aligned, entry)
        confidence = _compute_confidence(pt, trend_aligned, None, entry, meta)

        bi_finished = pt.get("biFinished", True)

        result.append({
            "id": build_dedupe_key(
                meta["varietyCode"],
                meta.get("executionTsCode") or meta.get("trendSeriesCode", ""),
                meta["timeframe"], pt["type"], pt.get("date", ""),
            ),
            "varietyCode": meta["varietyCode"],
            "displayName": meta["displayName"],
            "trendSeriesCode": meta["trendSeriesCode"],
            "executionTsCode": meta.get("executionTsCode"),
            "type": pt["type"],
            "direction": direction,
            "price": pt["price"],
            "entryPrice": entry["entryPrice"],
            "stopLoss": entry["stopLoss"],
            "takeProfit": entry["takeProfit"],
            "riskRewardRatio": entry["riskRewardRatio"],
            "date": pt.get("date", ""),
            "timeframe": meta["timeframe"],
            "seriesType": meta["seriesType"],
            "finished": bi_finished is not False,
            "biFinished": bi_finished,
            "confirmed": bi_finished is True,
            "source": "chanlun",
            "trendDirection": meta.get("trendDirection"),
            "trendAligned": trend_aligned,
            "confidence": confidence,
            "reasons": reasons,
            "divergenceType": pt.get("divergenceType"),
            "mappingDate": meta.get("mappingDate"),
            "status": "pending",
            "createdAt": datetime.now().isoformat(),
        })

    return result


# ===== Entry/SL/TP =====

def _compute_entry_details(pt, bis, hubs, klines, cfg, direction) -> dict:
    entry_price = pt.get("price", 0)
    atr = compute_atr(klines, 14)

    if direction == "long":
        near_hub = _find_nearest_hub(hubs, entry_price, "below")
        stop_loss = (min(near_hub["ZD"] - atr * 0.5, entry_price - atr * cfg["stopLossATRMultiple"])
                     if near_hub else entry_price - atr * cfg["stopLossATRMultiple"])
        near_hub_above = _find_nearest_hub(hubs, entry_price, "above")
        take_profit = (max(near_hub_above["ZG"] + atr * 0.5, entry_price + atr * cfg["takeProfitATRMultiple"])
                       if near_hub_above else entry_price + atr * cfg["takeProfitATRMultiple"])
    else:
        near_hub = _find_nearest_hub(hubs, entry_price, "above")
        stop_loss = (max(near_hub["ZG"] + atr * 0.5, entry_price + atr * cfg["stopLossATRMultiple"])
                     if near_hub else entry_price + atr * cfg["stopLossATRMultiple"])
        near_hub_below = _find_nearest_hub(hubs, entry_price, "below")
        take_profit = (min(near_hub_below["ZD"] - atr * 0.5, entry_price - atr * cfg["takeProfitATRMultiple"])
                       if near_hub_below else entry_price - atr * cfg["takeProfitATRMultiple"])

    stop_loss = round(stop_loss, 2)
    take_profit = round(take_profit, 2)
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 0

    return {"entryPrice": entry_price, "stopLoss": stop_loss, "takeProfit": take_profit,
            "riskRewardRatio": rr_ratio, "atr": atr}


def compute_atr(klines, period=14) -> float:
    if not klines or len(klines) < period + 1:
        if klines and len(klines) > 1:
            last = klines[-1]
            return (last["high"] - last["low"]) * 1.5
        return 10.0

    recent = klines[-(period + 1):]
    sum_tr = 0.0
    for i in range(1, len(recent)):
        curr, prev = recent[i], recent[i - 1]
        tr = max(curr["high"] - curr["low"],
                 abs(curr["high"] - prev["close"]),
                 abs(curr["low"] - prev["close"]))
        sum_tr += tr
    return sum_tr / period


def _find_nearest_hub(hubs, price, direction) -> dict | None:
    if not hubs:
        return None
    nearest = None
    min_dist = float("inf")
    for hub in hubs:
        if direction == "below" and hub["ZG"] < price:
            dist = price - hub["ZG"]
            if dist < min_dist:
                min_dist = dist
                nearest = hub
        if direction == "above" and hub["ZD"] > price:
            dist = hub["ZD"] - price
            if dist < min_dist:
                min_dist = dist
                nearest = hub
    return nearest


def _find_latest_structure_point(result, signal_date, direction) -> dict | None:
    if not result or not result.get("buySellPoints"):
        return None
    signal_ts = _parse_date(signal_date)
    allow = combo.is_buy_type if direction == "long" else combo.is_sell_type
    points = [pt for pt in result["buySellPoints"]
              if allow(pt["type"]) and _parse_date(pt.get("date")) <= signal_ts]
    return points[-1] if points else None


def _find_recent_hub(result, signal_date) -> dict | None:
    if not result or not result.get("hubs") or not result.get("mergedKlines"):
        return None
    ts = _parse_date(signal_date)
    hubs = result["hubs"]
    mk = result["mergedKlines"]
    for i in range(len(hubs) - 1, -1, -1):
        hub = hubs[i]
        end_idx = hub.get("endIndex", 0)
        end_bar = mk[end_idx] if end_idx < len(mk) else None
        if end_bar and _parse_date(end_bar.get("date")) <= ts:
            return hub
    return hubs[-1] if hubs else None


def _is_near_hub_edge(price, hub, direction, klines, cfg) -> bool:
    if not hub:
        return False
    atr = compute_atr(klines or [], 14)
    tolerance = max(atr * cfg.get("comboHourlyZoneATR", 0.8), 1)
    if direction == "long":
        return abs(price - hub["ZD"]) <= tolerance
    return abs(price - hub["ZG"]) <= tolerance


# ===== Scoring helpers =====

def _score_trend_alignment(trend_dir, direction, aligned_score, opposite_score) -> int:
    if not trend_dir or trend_dir == "consolidation":
        return 4
    if trend_dir == "up":
        return aligned_score if direction == "long" else opposite_score
    if trend_dir == "down":
        return aligned_score if direction == "short" else opposite_score
    return 0


def _score_signal_type(sig_type, execution_layer) -> int:
    score_map = {
        "buy1": 10, "sell1": 10, "buy2": 18, "sell2": 18,
        "buy3": 20, "sell3": 20, "semiBuy2": 11, "semiSell2": 11,
        "semiBuy3": 12, "semiSell3": 12,
    }
    base = score_map.get(sig_type, 8)
    return base if execution_layer else round(base * 0.7)


def _trend_label(trend_dir) -> str:
    if trend_dir == "up":
        return "偏多"
    if trend_dir == "down":
        return "偏空"
    return "盘整"


def _check_alignment(trend_dir, signal_type) -> bool:
    is_buy = "buy" in (signal_type or "").lower()
    is_sell = "sell" in (signal_type or "").lower()
    if trend_dir == "up" and is_buy:
        return True
    if trend_dir == "down" and is_sell:
        return True
    if trend_dir == "consolidation":
        return True
    return False


def _build_reasons(pt, meta, trend_aligned, entry) -> list[str]:
    reasons: list[str] = []
    is_buy = "buy" in pt["type"].lower()
    type_labels = {"buy1": "一买", "buy2": "二买", "buy3": "三买",
                   "sell1": "一卖", "sell2": "二卖", "sell3": "三卖"}
    type_label = type_labels.get(pt["type"], pt["type"])
    dir_label = "做多" if is_buy else "做空"

    reasons.append(f'{meta["displayName"]} {meta["timeframe"]} 出现缠论{type_label}信号 → {dir_label}')

    if pt.get("divergenceType") == "trend":
        reasons.append("趋势背驰确认（量价背离）")
    elif pt.get("divergenceType") == "consolidation":
        reasons.append("盘整背驰确认（中枢内背离）")

    if trend_aligned is True:
        td = meta.get("trendDirection", "")
        reasons.append(f'与{"上升" if td == "up" else "下降"}趋势方向一致')
    elif trend_aligned is False:
        td = meta.get("trendDirection", "")
        reasons.append(f'逆趋势信号（大周期{"上升" if td == "up" else "下降"}）')

    rr = entry["riskRewardRatio"]
    if rr >= 2:
        reasons.append(f"风险回报比 {rr}:1 较优")
    elif rr < 1.5:
        reasons.append(f"风险回报比 {rr}:1 偏低")

    if meta.get("seriesType") == "execution":
        reasons.append(f'入场周期 {meta["timeframe"]} 结构确认')

    return reasons


def _compute_confidence(pt, trend_aligned, seasonal, entry, meta) -> str:
    score = 50
    if pt["type"] in ("buy1", "sell1"):
        score += 15
    elif pt["type"] in ("buy2", "sell2"):
        score += 10
    elif pt["type"] in ("buy3", "sell3"):
        score += 5

    if pt.get("divergenceType") == "trend":
        score += 15
    elif pt.get("divergenceType") == "consolidation":
        score += 10

    if trend_aligned is True:
        score += 15
    elif trend_aligned is False:
        score -= 10

    rr = entry["riskRewardRatio"]
    if rr >= 3:
        score += 10
    elif rr >= 2:
        score += 5
    elif rr < 1:
        score -= 10

    if pt.get("biFinished") is not False:
        score += 5

    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


# ===== Utility =====

def build_dedupe_key(variety, ts_code, timeframe, signal_type, signal_date) -> str:
    return f"{variety}_{ts_code}_{timeframe}_{signal_type}_{signal_date}"


def deduplicate_signals(signals: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for s in signals:
        if s["id"] not in seen:
            seen[s["id"]] = s
    return list(seen.values())


def merge_signals(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new signals into existing candidate store.

    For the same (variety, direction) only the most recent signal is kept.
    This is the *candidate* view — what is actionable right now.
    """
    vd_latest: dict[str, dict] = {}
    for s in existing:
        key = f'{s.get("varietyCode", "")}_{s.get("direction", "")}'
        prev = vd_latest.get(key)
        if prev is None or s.get("date", "") > prev.get("date", ""):
            vd_latest[key] = s

    for ns in new:
        key = f'{ns.get("varietyCode", "")}_{ns.get("direction", "")}'
        prev = vd_latest.get(key)
        if prev is None or ns.get("date", "") >= prev.get("date", ""):
            vd_latest[key] = ns

    return sorted(vd_latest.values(), key=lambda s: s.get("date", ""), reverse=True)


def merge_signal_events(existing_events: list[dict],
                        new_signals: list[dict]) -> list[dict]:
    """Append new *confirmed* signals to the persistent event log.

    Events are never removed or overwritten. Each confirmed signal is
    frozen as a historical event with the data at the time of detection.
    Only signals with ``confirmed == True`` are recorded.
    """
    seen_ids: set[str] = {e.get("id", "") for e in existing_events}
    for sig in new_signals:
        if not sig.get("confirmed", False):
            continue
        if sig.get("id", "") in seen_ids:
            continue
        event = {**sig, "eventTime": datetime.now().isoformat(), "eventStatus": "active"}
        existing_events.append(event)
        seen_ids.add(sig.get("id", ""))
    return existing_events


def _find_bi_index(point, bis) -> int:
    date_val = point.get("date")
    if not date_val or not bis:
        return -1
    for i in range(len(bis) - 1, -1, -1):
        bi = bis[i]
        end_date = bi.get("endFractal", {}).get("date")
        if end_date and end_date <= date_val:
            return i
    return 0


def check_rollover_tolerance(signal_date, mapping_date, tolerance_days: int = 5) -> bool:
    if not signal_date or not mapping_date:
        return True
    sd = _parse_date(signal_date)
    md = _parse_date(mapping_date)
    diff_days = abs(sd - md) / (1000 * 60 * 60 * 24)
    return diff_days <= tolerance_days


def _tag_tradability(sig: dict) -> None:
    """Attach tradability info based on signal time and variety."""
    trade_check = combo.check_signal_tradability(
        sig.get("date"), sig.get("varietyCode", ""))
    sig["tradeable"] = trade_check["tradeable"]
    sig["tradeableNote"] = trade_check["reason"]
    sig["boundarySeverity"] = trade_check["severity"]
    if not trade_check["tradeable"]:
        sig["nextSession"] = trade_check["next_session"]
        sig["gapMinutes"] = trade_check["gap_minutes"]
        sig["suggestion"] = trade_check["suggestion"]
        sig["reasons"] = sig.get("reasons", []) + [
            f'⚠ {trade_check["reason"]}',
            f'建议: {trade_check["suggestion"]}',
        ]


def _parse_date(s) -> int:
    if not s:
        return 0
    try:
        if "-" in str(s):
            return int(datetime.fromisoformat(str(s).replace(" ", "T")).timestamp() * 1000)
        sv = str(s)
        y, m, d = int(sv[:4]), int(sv[4:6]), int(sv[6:8])
        return int(datetime(y, m, d).timestamp() * 1000)
    except Exception:
        return 0
