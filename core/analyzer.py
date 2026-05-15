"""Unified Chanlun analyzer (缠论统一分析入口).

Pipeline: raw K-lines -> merged K-lines -> fractals -> bi -> segments
          -> hubs -> buy/sell points, with MACD, trend classification,
          completeness assessment, and multi-period analysis.
"""

from __future__ import annotations

import math
import time

from .types import BuySellType, BUYSELL_LABELS, ChanlunDirection, DivergenceType
from .merge_kline import merge_klines
from .fractal import find_fractals
from .bi import build_bis
from .segment import build_segments
from .hub import find_hubs, upgrade_overlapping_hubs
from .buysell import find_buysell_points
from .macd import calculate_macd


class ChanlunAnalyzer:
    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self.config: dict = {
            "biMode": "new",
            "segEndMode": "both",
        }

    def set_config(self, new_config: dict) -> None:
        self.config.update(new_config)
        self.clear_cache()

    def analyze(self, klines: list[dict], cache_key: str | None = None) -> dict:
        if cache_key and cache_key in self._cache:
            return self._cache[cache_key]

        if not klines or len(klines) < 10:
            return self._empty_result()

        t0 = time.monotonic()

        merged_klines = merge_klines(klines)
        fractals = find_fractals(merged_klines)
        bis = build_bis(fractals, merged_klines, self.config)
        segments = build_segments(bis, self.config)
        raw_hubs = find_hubs(bis)
        hubs = upgrade_overlapping_hubs(raw_hubs)

        macd_data = calculate_macd(klines)
        buysell_points = find_buysell_points(bis, hubs, merged_klines, macd_data, self.config)

        trend_info = self._classify_trend(bis, hubs)
        segment_hubs = self._build_segment_level_hubs(segments)
        completeness = self._assess_completeness(bis, hubs, macd_data)

        duration_ms = round((time.monotonic() - t0) * 1000)

        result = {
            "mergedKlines": merged_klines,
            "fractals": fractals,
            "bis": bis,
            "segments": segments,
            "rawHubs": raw_hubs,
            "hubs": hubs,
            "buySellPoints": buysell_points,
            "currentTrend": trend_info["trend"],
            "movementType": trend_info["movementType"],
            "segmentHubs": segment_hubs,
            "macdData": macd_data,
            "completeness": completeness,
            "config": dict(self.config),
            "duration": duration_ms,
        }

        if cache_key:
            self._cache[cache_key] = result

        return result

    # ------------------------------------------------------------------
    # Trend classification
    # ------------------------------------------------------------------

    def _classify_trend(self, bis: list[dict], hubs: list[dict]) -> dict:
        if len(bis) < 2:
            return {"trend": "consolidation", "movementType": "盘整"}

        last_bi = bis[-1]

        if not hubs:
            if last_bi["direction"] == ChanlunDirection.UP:
                return {"trend": "up", "movementType": "上涨"}
            return {"trend": "down", "movementType": "下跌"}

        if len(hubs) >= 2:
            up_trend = True
            down_trend = True
            for i in range(1, len(hubs)):
                if hubs[i]["ZD"] <= hubs[i - 1]["ZG"]:
                    up_trend = False
                if hubs[i]["ZG"] >= hubs[i - 1]["ZD"]:
                    down_trend = False

            if up_trend:
                return {"trend": "up", "movementType": "趋势上涨"}
            if down_trend:
                return {"trend": "down", "movementType": "趋势下跌"}

        last_hub = hubs[-1]
        if last_bi["low"] >= last_hub["ZG"]:
            return {"trend": "up", "movementType": "盘整上涨"}
        if last_bi["high"] <= last_hub["ZD"]:
            return {"trend": "down", "movementType": "盘整下跌"}

        return {"trend": "consolidation", "movementType": "盘整"}

    # ------------------------------------------------------------------
    # Completeness assessment
    # ------------------------------------------------------------------

    def _assess_completeness(self, bis: list[dict], hubs: list[dict],
                             macd_data: dict | None) -> dict:
        if len(bis) < 5 or not hubs:
            return {"score": 0, "level": "unknown", "factors": []}

        factors: list[str] = []
        score = 0

        trend_hub_count = self._count_trend_hubs(hubs)
        if trend_hub_count >= 3:
            score += 35
            factors.append("三个以上中枢，趋势老化")
        elif trend_hub_count >= 2:
            score += 20
            factors.append("两个中枢，具备背驰条件")

        last_bis = bis[-6:]
        same_dir_pairs = self._find_same_direction_pairs(last_bis)
        for curr, prev in same_dir_pairs:
            curr_range = abs(curr["high"] - curr["low"])
            prev_range = abs(prev["high"] - prev["low"])
            if curr_range < prev_range * 0.7:
                score += 25
                factors.append("最近笔幅度明显缩小")
                break

        if macd_data and macd_data.get("dif") and macd_data.get("dea"):
            recent = [v for v in macd_data["dif"][-10:] if v is not None]
            recent_dea = [v for v in macd_data["dea"][-10:] if v is not None]
            if len(recent) >= 5 and len(recent_dea) >= 5:
                dif_dea_dist = abs(recent[-1] - recent_dea[-1])
                prev_dist = abs(recent[len(recent) // 2] - recent_dea[len(recent_dea) // 2])
                if dif_dea_dist < prev_dist * 0.5:
                    score += 20
                    factors.append("MACD能量收敛，DIF逼近DEA")

        last_hub = hubs[-1]
        if last_hub.get("extended"):
            score += 15
            factors.append("中枢已延伸，震荡过久")

        level = "high" if score >= 60 else ("medium" if score >= 30 else "low")
        return {"score": min(score, 100), "level": level, "factors": factors}

    def _count_trend_hubs(self, hubs: list[dict]) -> int:
        if len(hubs) < 2:
            return len(hubs)
        count = 1
        for i in range(1, len(hubs)):
            no_overlap = hubs[i]["ZG"] < hubs[i - 1]["ZD"] or hubs[i]["ZD"] > hubs[i - 1]["ZG"]
            if no_overlap:
                count += 1
        return count

    def _find_same_direction_pairs(self, bis: list[dict]) -> list[tuple[dict, dict]]:
        pairs: list[tuple[dict, dict]] = []
        i = len(bis) - 1
        while i >= 2:
            if bis[i]["direction"] == bis[i - 2]["direction"]:
                pairs.append((bis[i], bis[i - 2]))
            i -= 2
        return pairs

    # ------------------------------------------------------------------
    # Segment-level hubs
    # ------------------------------------------------------------------

    def _build_segment_level_hubs(self, segments: list[dict]) -> list[dict]:
        if not segments or len(segments) < 3:
            return []

        seg_hubs: list[dict] = []
        i = 0

        while i < len(segments) - 2:
            s1, s2, s3 = segments[i], segments[i + 1], segments[i + 2]
            zg = min(s1["high"], s2["high"], s3["high"])
            zd = max(s1["low"], s2["low"], s3["low"])

            if zg <= zd:
                i += 1
                continue

            hub_segs = [s1, s2, s3]
            end_idx = i + 2

            for j in range(i + 3, len(segments)):
                sj = segments[j]
                if sj["high"] > zd and sj["low"] < zg:
                    hub_segs.append(sj)
                    end_idx = j
                else:
                    break

            gg = max(s["high"] for s in hub_segs)
            dd = min(s["low"] for s in hub_segs)

            seg_hubs.append({
                "ZG": zg,
                "ZD": zd,
                "GG": gg,
                "DD": dd,
                "startIndex": s1["startBi"]["startFractal"]["klineIndex"],
                "endIndex": hub_segs[-1]["endBi"]["endFractal"]["klineIndex"],
                "segments": hub_segs,
                "level": 2,
            })

            i = end_idx + 1

        return seg_hubs

    # ------------------------------------------------------------------
    # Multi-period analysis
    # ------------------------------------------------------------------

    def analyze_multi_period(self, multi_data: dict[str, list[dict]]) -> dict:
        results: dict[str, dict] = {}
        timeframes = list(multi_data.keys())

        for tf in timeframes:
            klines = multi_data[tf]
            if klines and len(klines) >= 10:
                results[tf] = self.analyze(klines)

        summary = self._build_multi_period_summary(results, timeframes)
        return {**results, "summary": summary}

    def _build_multi_period_summary(self, results: dict[str, dict],
                                    timeframes: list[str]) -> dict:
        tf_order = ["15m", "1h", "1d", "1w", "1M"]
        sorted_tfs = sorted(timeframes, key=lambda t: tf_order.index(t) if t in tf_order else 99)

        summary: dict = {"periods": {}}

        for tf in sorted_tfs:
            r = results.get(tf)
            if not r:
                continue

            latest_bs = r["buySellPoints"][-1] if r["buySellPoints"] else None
            summary["periods"][tf] = {
                "trend": r["currentTrend"],
                "movementType": r["movementType"],
                "hubCount": len(r["hubs"]),
                "biCount": len(r["bis"]),
                "segmentCount": len(r["segments"]),
                "latestSignal": {
                    "type": latest_bs["type"],
                    "price": latest_bs["price"],
                    "date": latest_bs["date"],
                } if latest_bs else None,
                "activeHub": {
                    "ZG": r["hubs"][-1]["ZG"],
                    "ZD": r["hubs"][-1]["ZD"],
                } if r["hubs"] else None,
            }

        if len(sorted_tfs) >= 2:
            large_tf = sorted_tfs[-1]
            small_tf = sorted_tfs[0]
            if large_tf in results and small_tf in results:
                large_trend = results[large_tf]["currentTrend"]
                small_trend = results[small_tf]["currentTrend"]
                summary["alignment"] = "aligned" if large_trend == small_trend else "divergent"
                summary["largeTrend"] = large_trend
                summary["smallTrend"] = small_trend

        summary["nestedSignals"] = self._detect_interval_nesting(results, sorted_tfs)
        summary["guidance"] = self._generate_level_guidance(results, sorted_tfs)

        return summary

    def _detect_interval_nesting(self, results: dict[str, dict],
                                 sorted_tfs: list[str]) -> list[dict]:
        nested_signals: list[dict] = []
        if len(sorted_tfs) < 2:
            return nested_signals

        buy_types = {BuySellType.BUY1, BuySellType.BUY2, BuySellType.BUY3}
        sell_types = {BuySellType.SELL1, BuySellType.SELL2, BuySellType.SELL3}

        for i in range(len(sorted_tfs) - 1, 0, -1):
            large_tf = sorted_tfs[i]
            small_tf = sorted_tfs[i - 1]
            large_result = results.get(large_tf)
            small_result = results.get(small_tf)
            if not large_result or not small_result:
                continue

            for large_pt in large_result["buySellPoints"]:
                is_buy = large_pt["type"] in buy_types
                is_sell = large_pt["type"] in sell_types
                if not is_buy and not is_sell:
                    continue

                match_window = self._get_time_window(large_tf)
                compatible_types = buy_types if is_buy else sell_types
                matched = [
                    sp for sp in small_result["buySellPoints"]
                    if sp["type"] in compatible_types
                    and abs(self._date_to_timestamp(sp["date"]) - self._date_to_timestamp(large_pt["date"])) < match_window
                ]

                if matched:
                    is_primary = large_pt["type"] in (BuySellType.BUY1, BuySellType.SELL1)
                    confidence = "high" if is_primary and large_pt.get("divergenceType") == "trend" else (
                        "medium" if is_primary else "low"
                    )
                    nested_signals.append({
                        "largeTf": large_tf,
                        "smallTf": small_tf,
                        "largeSignal": {"type": large_pt["type"], "price": large_pt["price"], "date": large_pt["date"]},
                        "smallSignal": {"type": matched[0]["type"], "price": matched[0]["price"], "date": matched[0]["date"]},
                        "confidence": confidence,
                        "nestingLevel": len(sorted_tfs) - i + 1,
                    })

        return nested_signals

    def _generate_level_guidance(self, results: dict[str, dict],
                                 sorted_tfs: list[str]) -> dict:
        guidance: dict = {"levels": [], "action": "", "confidence": "low", "reasons": []}
        tf_names = {"1M": "月线", "1w": "周线", "1d": "日线", "1h": "小时线", "15m": "15分钟"}
        level_order = ["1M", "1w", "1d", "1h", "15m"]

        bull_score = 0
        bear_score = 0

        def tf_analysis(tf: str) -> dict | None:
            r = results.get(tf)
            if not r:
                return None
            last_bi = r["bis"][-1] if r["bis"] else None
            last_hub = r["hubs"][-1] if r["hubs"] else None
            buy_pts = [p for p in r["buySellPoints"]
                       if p["type"] in (BuySellType.BUY1, BuySellType.BUY2, BuySellType.BUY3)]
            sell_pts = [p for p in r["buySellPoints"]
                        if p["type"] in (BuySellType.SELL1, BuySellType.SELL2, BuySellType.SELL3)]
            return {
                "trend": r["currentTrend"],
                "movementType": r["movementType"],
                "hubCount": len(r["hubs"]),
                "biCount": len(r["bis"]),
                "lastBiDir": last_bi.get("direction") if last_bi else None,
                "lastBiFinished": last_bi.get("finished") is not False if last_bi else True,
                "lastHub": last_hub,
                "completeness": r.get("completeness"),
                "recentBuy": buy_pts[-1] if buy_pts else None,
                "recentSell": sell_pts[-1] if sell_pts else None,
            }

        for tf in level_order:
            a = tf_analysis(tf)
            if not a:
                continue

            level = {"tf": tf, "name": tf_names.get(tf, tf), "trend": a["trend"],
                     "movementType": a["movementType"], "note": ""}

            if tf in ("1M", "1w"):
                weight = 30 if tf == "1M" else 20
                if a["trend"] == "up":
                    bull_score += weight
                    level["note"] = f'{level["name"]}上涨趋势，大方向偏多'
                elif a["trend"] == "down":
                    bear_score += weight
                    level["note"] = f'{level["name"]}下跌趋势，大方向偏空'
                else:
                    level["note"] = f'{level["name"]}盘整'
                    if a["completeness"] and a["completeness"]["score"] >= 60:
                        level["note"] += "，走势完成度较高，可能变盘"
                if a["recentBuy"]:
                    bull_score += 10
                    label = BUYSELL_LABELS.get(a["recentBuy"]["type"], a["recentBuy"]["type"])
                    level["note"] += f"，近期有{label}信号"
                if a["recentSell"]:
                    bear_score += 10
                    label = BUYSELL_LABELS.get(a["recentSell"]["type"], a["recentSell"]["type"])
                    level["note"] += f"，近期有{label}信号"

            elif tf == "1d":
                if a["trend"] == "up":
                    bull_score += 15
                    level["note"] = "日线上涨结构"
                elif a["trend"] == "down":
                    bear_score += 15
                    level["note"] = "日线下跌结构"
                else:
                    level["note"] = "日线盘整"
                if a["hubCount"] > 0:
                    level["note"] += f'，{a["hubCount"]}个中枢'
                if a["recentBuy"]:
                    bull_score += 8
                    level["note"] += f'，出现{BUYSELL_LABELS.get(a["recentBuy"]["type"], "")}'
                if a["recentSell"]:
                    bear_score += 8
                    level["note"] += f'，出现{BUYSELL_LABELS.get(a["recentSell"]["type"], "")}'

            elif tf == "1h":
                if a["completeness"]:
                    level["note"] = f'走势完成度 {a["completeness"]["score"]}%'
                    if a["completeness"]["score"] >= 60:
                        level["note"] += "（高）"
                if a["trend"] == "up":
                    bull_score += 5
                if a["trend"] == "down":
                    bear_score += 5
                if a["recentBuy"]:
                    bull_score += 5
                    level["note"] += f'，{BUYSELL_LABELS.get(a["recentBuy"]["type"], "")}'
                if a["recentSell"]:
                    bear_score += 5
                    level["note"] += f'，{BUYSELL_LABELS.get(a["recentSell"]["type"], "")}'

            elif tf == "15m":
                signals: list[str] = []
                if a["recentBuy"]:
                    bull_score += 5
                    signals.append(f'{BUYSELL_LABELS.get(a["recentBuy"]["type"], "")} @ {a["recentBuy"]["price"]:.2f}')
                if a["recentSell"]:
                    bear_score += 5
                    signals.append(f'{BUYSELL_LABELS.get(a["recentSell"]["type"], "")} @ {a["recentSell"]["price"]:.2f}')
                level["note"] = f'入场信号: {", ".join(signals)}' if signals else "暂无明确入场信号"
                if a["lastHub"]:
                    level["note"] += f' | 中枢[{a["lastHub"]["ZD"]:.2f}, {a["lastHub"]["ZG"]:.2f}]'

            guidance["levels"].append(level)

        total_score = bull_score + bear_score
        if total_score == 0:
            guidance["action"] = "数据不足，无法给出操作建议"
            guidance["confidence"] = "low"
        else:
            bull_ratio = bull_score / total_score
            bear_ratio = bear_score / total_score

            if bull_ratio > 0.7:
                guidance["action"] = "多头占优，可关注15分钟级别的买点入场做多"
                guidance["confidence"] = "high" if bull_score >= 50 else "medium"
                guidance["reasons"].append("多周期趋势偏多")
            elif bear_ratio > 0.7:
                guidance["action"] = "空头占优，可关注15分钟级别的卖点入场做空"
                guidance["confidence"] = "high" if bear_score >= 50 else "medium"
                guidance["reasons"].append("多周期趋势偏空")
            elif bull_ratio > 0.55:
                guidance["action"] = "偏多震荡，可在15分钟找二买/三买轻仓试多"
                guidance["confidence"] = "medium"
                guidance["reasons"].append("大周期偏多但小周期尚未确认")
            elif bear_ratio > 0.55:
                guidance["action"] = "偏空震荡，可在15分钟找二卖/三卖轻仓试空"
                guidance["confidence"] = "medium"
                guidance["reasons"].append("大周期偏空但小周期尚未确认")
            else:
                guidance["action"] = "多空分歧，建议观望等待方向明确"
                guidance["confidence"] = "low"
                guidance["reasons"].append("多周期走势不一致")

            monthly = tf_analysis("1M")
            entry15m = tf_analysis("15m")
            if monthly and entry15m:
                if monthly["trend"] == "up" and entry15m["recentBuy"]:
                    guidance["reasons"].append(
                        f'月线上涨 + 15分钟{BUYSELL_LABELS.get(entry15m["recentBuy"]["type"], "")}共振')
                    guidance["confidence"] = "high"
                if monthly["trend"] == "down" and entry15m["recentSell"]:
                    guidance["reasons"].append(
                        f'月线下跌 + 15分钟{BUYSELL_LABELS.get(entry15m["recentSell"]["type"], "")}共振')
                    guidance["confidence"] = "high"

        guidance["bullScore"] = bull_score
        guidance["bearScore"] = bear_score
        return guidance

    # ------------------------------------------------------------------

    def _get_time_window(self, tf: str) -> int:
        windows = {"1M": 30 * 86400000, "1w": 7 * 86400000, "1d": 86400000,
                    "1h": 4 * 3600000, "15m": 3600000}
        return windows.get(tf, 86400000)

    def _date_to_timestamp(self, date_str: str | None) -> int:
        if not date_str:
            return 0
        import re
        cleaned = re.sub(r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", date_str)
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(cleaned.replace(" ", "T") if "T" not in cleaned else cleaned)
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    def _empty_result(self) -> dict:
        return {
            "mergedKlines": [],
            "fractals": [],
            "bis": [],
            "segments": [],
            "hubs": [],
            "buySellPoints": [],
            "currentTrend": "consolidation",
            "movementType": "盘整",
            "segmentHubs": [],
            "macdData": None,
            "completeness": None,
            "config": dict(self.config),
            "duration": 0,
        }

    def clear_cache(self) -> None:
        self._cache.clear()
