"""V14 Multi-timeframe Chanlun backtest engine.

Ported from renderer/js/strategy/backtest-engine.js.
Supports monthly/weekly/daily/hourly/entry timeframe alignment scoring,
bi-reversal and hub-bounce structural entries, ATR-based risk management
with trailing stops, and comprehensive result statistics.
"""

from __future__ import annotations

import math
from datetime import datetime

from ..core.types import BUYSELL_LABELS

DEFAULT_PARAMS = {
    "atrPeriod": 14,
    "initialCapital": 100000,
    "contractMultiplier": 10,
    "commissionPerLot": 1.21,
    "strategyMode": "chanlunMultiTF_V14",
    "v14Preset": "balanced",
    "v14EnableType3": False,
    "v14EntryWindow": 2,
    "v14StopATR": 1.5,
    "v14TargetATR": 3.0,
    "v14TrailATR": 1.0,
    "v14MaxHoldBars": 40,
    "v14Cooldown": 2,
    "v14MinAlignScore": 25,
}

LONG_TYPES = {"buy1", "buy2", "buy3", "semiBuy2", "semiBuy3", "biReversalLong", "hubBounceLong"}
SHORT_TYPES = {"sell1", "sell2", "sell3", "semiSell2", "semiSell3", "biReversalShort", "hubBounceShort"}
BASE_TYPES = {"buy1", "buy2", "sell1", "sell2", "semiBuy2", "semiSell2"}
TYPE3_TYPES = {"buy3", "sell3", "semiBuy3", "semiSell3"}


class BacktestEngine:
    def __init__(self) -> None:
        self.default_params = dict(DEFAULT_PARAMS)

    @staticmethod
    def _rp(val, cfg):
        """Round price to appropriate decimal places (0 for futures, 2 for stocks)."""
        d = cfg.get("priceDecimals", 0)
        return round(val, d)

    def run(self, klines: list[dict], params: dict | None = None,
            analysis_context: dict | None = None, start_idx: int = 0) -> dict:
        if not klines or len(klines) < 50:
            return self._empty_result("数据不足，至少需要 50 根 K 线")

        cfg = {**self.default_params, **(params or {})}
        data = list(klines)
        safe_start = max(0, min(start_idx, len(data) - 1))
        return self._run_v14(data, cfg, analysis_context, safe_start)

    def snapshot(self, klines: list[dict], params: dict | None = None,
                 analysis_context: dict | None = None,
                 start_idx: int = 0) -> dict:
        """Run the V14 engine without force-closing the last open position."""
        if not klines or len(klines) < 50:
            return self._empty_result("数据不足，至少需要 50 根 K 线")

        cfg = {**self.default_params, **(params or {})}
        data = list(klines)
        safe_start = max(0, min(start_idx, len(data) - 1))
        return self._run_v14_core(data, cfg, analysis_context, safe_start,
                                  force_close_end=False)

    def _run_v14(self, data, cfg, ctx, start_idx) -> dict:
        return self._run_v14_core(data, cfg, ctx, start_idx,
                                  force_close_end=True)

    def _run_v14_core(self, data, cfg, ctx, start_idx,
                      force_close_end: bool) -> dict:
        runtime = self._build_v14_runtime(data, cfg, ctx)
        if runtime.get("error"):
            return self._empty_result(runtime["error"])

        state = self._init_v14_state(cfg)
        for i in range(runtime["length"]):
            self._advance_v14_state(runtime, state, i, start_idx)

        self._finalize_v14_state(runtime, state, force_close_end=force_close_end)
        result = self._build_result(
            state["trades"], state["signals"], state["equity"], cfg, data
        )
        result["openPosition"] = state["position"]
        result["cooldownUntil"] = state["cooldown_until"]
        result["lastProcessedIndex"] = runtime["length"] - 1 if runtime["length"] > 0 else -1
        return result

    def _build_v14_runtime(self, data, cfg, ctx) -> dict:
        if not ctx or not ctx.get("multiPeriod"):
            return {"error": "V14 需要多周期缠论分析上下文"}

        mp = ctx["multiPeriod"]
        entry_tf = cfg.get("v14EntryTimeframe")
        if not entry_tf:
            entry_tf = "5m" if "5m" in mp else ("15m" if "15m" in mp else ("1h" if "1h" in mp else None))
        if not entry_tf or entry_tf not in mp:
            return {"error": "V14 需要入场周期数据 (5m/15m/1h)"}

        entry_result = mp[entry_tf]["result"]
        sub_struct_result = mp.get("15m", {}).get("result") if entry_tf == "5m" and "15m" in mp else None
        hourly_result = mp.get("1h", {}).get("result") if entry_tf != "1h" else None
        daily_result = mp.get("1d", {}).get("result")
        weekly_result = mp.get("1w", {}).get("result")
        monthly_result = mp.get("1M", {}).get("result")

        if not entry_result or not entry_result.get("bis") or len(entry_result["bis"]) < 5:
            return {"error": f"{entry_tf}缠论分析数据不足"}

        custom_types = cfg.get("v14SignalTypes")
        if custom_types:
            allowed = set(custom_types)
        elif cfg["v14EnableType3"]:
            allowed = BASE_TYPES | TYPE3_TYPES
        else:
            allowed = BASE_TYPES
        long_only = cfg.get("longOnly", False)
        if long_only:
            allowed = allowed & LONG_TYPES
        disable_structural = bool(custom_types)

        bsp_list = self._build_bsp_list(data, entry_result, allowed)
        bi_end_map = self._build_bi_end_map(data, entry_result)
        hubs_entry = self._build_hub_list(data, entry_result)
        hubs_sub = self._build_hub_list(data, sub_struct_result) if sub_struct_result else []
        hubs_h = self._build_hub_list(data, hourly_result) if hourly_result else []
        if entry_tf == "5m":
            hub_lists = [hubs_entry, hubs_sub, hubs_h]
        elif entry_tf == "1h":
            hub_lists = [hubs_entry]
        else:
            hub_lists = [hubs_entry, hubs_h]

        return {
            "data": data,
            "cfg": cfg,
            "length": len(data),
            "atr": self._calc_atr(data, cfg["atrPeriod"]),
            "entry_tf": entry_tf,
            "daily_result": daily_result,
            "weekly_result": weekly_result,
            "monthly_result": monthly_result,
            "hourly_result": hourly_result,
            "bsp_list": bsp_list,
            "bi_end_map": bi_end_map,
            "hub_lists": hub_lists,
            "long_only": long_only,
            "disable_structural": disable_structural,
        }

    def _init_v14_state(self, cfg) -> dict:
        return {
            "capital": cfg["initialCapital"],
            "position": None,
            "cooldown_until": 0,
            "bsp_cursor": 0,
            "active_bsp": None,
            "active_bsp_bar": -100,
            "trades": [],
            "signals": [],
            "equity": [],
        }

    def _advance_v14_state(self, runtime, state, i, start_idx) -> None:
        data = runtime["data"]
        cfg = runtime["cfg"]
        bar = data[i]
        atr = runtime["atr"]
        cur_atr = atr[i] if i < len(atr) else (atr[-1] if atr else 1)
        bsp_list = runtime["bsp_list"]

        while state["bsp_cursor"] < len(bsp_list) and bsp_list[state["bsp_cursor"]]["origTime"] <= bar.get("time", 0):
            state["active_bsp"] = bsp_list[state["bsp_cursor"]]
            state["active_bsp_bar"] = i
            state["bsp_cursor"] += 1

        if i < start_idx:
            return

        position = state["position"]
        if position:
            position = self._update_position(position, bar, i, cfg, cur_atr)
            if position.get("closed"):
                pnl = self._calc_pnl(position, cfg)
                state["capital"] += pnl
                state["trades"].append({
                    **position,
                    "exitDate": bar["date"],
                    "exitIndex": i,
                    "exitContract": bar.get("contract", position.get("exitContract", "")),
                    "tradedContract": position.get("entryContract", bar.get("contract", "")),
                    "pnl": pnl,
                    "capitalAfter": state["capital"],
                })
                state["signals"].append({
                    "date": bar["date"],
                    "index": i,
                    "signal": "平多" if position["direction"] == "long" else "平空",
                    "price": position["exitPrice"],
                })
                state["cooldown_until"] = i + cfg["v14Cooldown"]
                position = None
        state["position"] = position

        eq_val = state["capital"] + (self._unrealized_pnl(position, bar, cfg) if position else 0)
        state["equity"].append({"date": bar["date"], "index": i, "value": eq_val})

        if position or i < state["cooldown_until"]:
            return

        m_bias = self._get_tf_bias(runtime["monthly_result"], bar.get("time", 0))
        w_bias = self._get_tf_bias(runtime["weekly_result"], bar.get("time", 0))
        d_bias = self._get_tf_bias(runtime["daily_result"], bar.get("time", 0))
        h_bias = self._get_tf_bias(runtime["hourly_result"], bar.get("time", 0))

        entry = None
        active_bsp = state["active_bsp"]
        if active_bsp and not active_bsp.get("consumed") and (i - state["active_bsp_bar"]) <= cfg["v14EntryWindow"]:
            is_buy = active_bsp["type"] in LONG_TYPES
            is_sell = active_bsp["type"] in SHORT_TYPES
            if is_buy or is_sell:
                direction = "long" if is_buy else "short"
                al = self._score_alignment(
                    direction, m_bias, w_bias, d_bias, h_bias,
                    active_bsp["type"], runtime["daily_result"], bar.get("time", 0)
                )
                if al["score"] >= cfg["v14MinAlignScore"]:
                    active_bsp["consumed"] = True
                    entry = {"bsPoint": active_bsp, "alignment": al, "direction": direction}

        if not entry and not runtime["disable_structural"]:
            entry = self._eval_structural_entry(
                i, data, bar, cur_atr, cfg, runtime["bi_end_map"], runtime["hub_lists"],
                m_bias, w_bias, d_bias, h_bias, runtime["daily_result"], runtime["entry_tf"]
            )

        if not entry:
            return
        if runtime["long_only"] and entry["direction"] != "long":
            return

        position = self._open_position(entry["bsPoint"], entry["alignment"], bar, i, cfg, cur_atr)
        sig_label = BUYSELL_LABELS.get(
            entry["bsPoint"]["type"],
            entry["bsPoint"].get("description", entry["bsPoint"]["type"]),
        )
        state["signals"].append({
            "date": bar["date"],
            "index": i,
            "signal": f'{"开多" if entry["direction"] == "long" else "开空"}({sig_label})',
            "price": position["entryPrice"],
        })
        state["position"] = position
        state["equity"][-1]["value"] = state["capital"] + self._unrealized_pnl(position, bar, cfg)

    def _finalize_v14_state(self, runtime, state, *, force_close_end: bool) -> None:
        position = state["position"]
        if not position or not force_close_end:
            return

        data = runtime["data"]
        cfg = runtime["cfg"]
        last_bar = data[-1]
        position["exitPrice"] = self._rp(last_bar["close"], cfg)
        position["closed"] = True
        position["exitReason"] = "回测结束"
        pnl = self._calc_pnl(position, cfg)
        state["capital"] += pnl
        state["trades"].append({
            **position,
            "exitDate": last_bar["date"],
            "exitIndex": runtime["length"] - 1,
            "exitContract": last_bar.get("contract", ""),
            "tradedContract": position.get("entryContract", ""),
            "pnl": pnl,
            "capitalAfter": state["capital"],
        })
        state["signals"].append({
            "date": last_bar["date"],
            "index": runtime["length"] - 1,
            "signal": "强制平仓",
            "price": last_bar["close"],
        })
        if state["equity"]:
            state["equity"][-1]["value"] = state["capital"]
        state["position"] = None

    # ------------------------------------------------------------------
    # BSP / Hub list builders
    # ------------------------------------------------------------------

    def _build_bsp_list(self, data, entry_result, allowed):
        mk = entry_result.get("mergedKlines", [])
        date_to_idx = {d["date"]: i for i, d in enumerate(data)}
        lst = []
        for pt in entry_result.get("buySellPoints", []):
            if pt["type"] not in allowed:
                continue
            orig_time = 0
            if pt.get("date") and pt["date"] in date_to_idx:
                orig_time = data[date_to_idx[pt["date"]]].get("time", 0)
            elif pt.get("index", 0) < len(mk):
                mk_bar = mk[pt["index"]]
                if mk_bar["date"] in date_to_idx:
                    orig_time = data[date_to_idx[mk_bar["date"]]].get("time", 0)
                else:
                    orig_time = mk_bar.get("time") or self._parse_ts(mk_bar["date"])
            if orig_time > 0:
                lst.append({**pt, "origTime": orig_time, "consumed": False})
        lst.sort(key=lambda x: x["origTime"])
        return lst

    def _build_bi_end_map(self, data, entry_result):
        result_map = {}
        if not entry_result or not entry_result.get("bis"):
            return result_map
        date_to_idx = {d["date"]: i for i, d in enumerate(data)}
        mk = entry_result.get("mergedKlines", [])
        for bi in entry_result["bis"]:
            ef = bi.get("endFractal")
            if not ef or bi.get("finished") is False:
                continue
            mk_idx = ef.get("klineIndex", 0)
            orig_idx = None
            if ef.get("date") and ef["date"] in date_to_idx:
                orig_idx = date_to_idx[ef["date"]]
            elif mk_idx < len(mk) and mk[mk_idx]["date"] in date_to_idx:
                orig_idx = date_to_idx[mk[mk_idx]["date"]]
            if orig_idx is not None:
                result_map[orig_idx] = bi
        return result_map

    def _build_hub_list(self, data, tf_result):
        lst = []
        if not tf_result or not tf_result.get("hubs"):
            return lst
        date_to_idx = {d["date"]: i for i, d in enumerate(data)}
        mk = tf_result.get("mergedKlines", [])
        for hub in tf_result["hubs"]:
            hub_bis = hub.get("bis", [])
            if not hub_bis:
                continue
            last_bi = hub_bis[-1]
            ef = last_bi.get("endFractal")
            if not ef:
                continue
            mk_idx = ef.get("klineIndex", 0)
            end_idx = None
            if ef.get("date") and ef["date"] in date_to_idx:
                end_idx = date_to_idx[ef["date"]]
            elif mk_idx < len(mk) and mk[mk_idx]["date"] in date_to_idx:
                end_idx = date_to_idx[mk[mk_idx]["date"]]
            if end_idx is None:
                frac_date = ef.get("date") or (mk[mk_idx]["date"] if mk_idx < len(mk) else None)
                if frac_date:
                    frac_time = self._parse_ts(frac_date)
                    if frac_time:
                        for j in range(len(data) - 1, -1, -1):
                            bt = data[j].get("time") or self._parse_ts(data[j]["date"])
                            if bt <= frac_time:
                                end_idx = j
                                break
            if end_idx is not None:
                lst.append({"hub": hub, "endIdx": end_idx})
        return lst

    # ------------------------------------------------------------------
    # Structural entry evaluation
    # ------------------------------------------------------------------

    def _eval_structural_entry(self, i, data, bar, cur_atr, cfg, bi_end_map,
                               hub_lists, m_bias, w_bias, d_bias, h_bias,
                               daily_result, entry_tf):
        bi = bi_end_map.get(i)
        if bi:
            bi_range = abs(bi["high"] - bi["low"])
            if bi_range >= cur_atr * 0.3:
                d = "long" if bi["direction"] == "down" else "short"
                sig_type = "biReversalLong" if d == "long" else "biReversalShort"
                al = self._score_alignment(d, m_bias, w_bias, d_bias, h_bias, sig_type, daily_result, bar.get("time", 0))
                if al["score"] >= cfg["v14MinAlignScore"]:
                    return {
                        "bsPoint": {"type": sig_type, "price": bi["low"] if d == "long" else bi["high"],
                                    "description": "笔底反转" if d == "long" else "笔顶反转"},
                        "alignment": al, "direction": d,
                    }

        tf_labels = ["5m", "15m", "1h"] if entry_tf == "5m" else [entry_tf, "1h"]
        for li, hub_list in enumerate(hub_lists):
            active_hub = None
            active_hub_age = float("inf")
            for h_idx in range(len(hub_list) - 1, -1, -1):
                if hub_list[h_idx]["endIdx"] <= i:
                    active_hub = hub_list[h_idx]["hub"]
                    active_hub_age = i - hub_list[h_idx]["endIdx"]
                    break

            max_age = 400 if li == len(hub_lists) - 1 else (100 if li == 0 else 200)
            if not active_hub or active_hub_age > max_age:
                continue

            tol = cur_atr * 0.3
            body = abs(bar["close"] - bar["open"])
            label = tf_labels[li] if li < len(tf_labels) else ""

            if (bar["low"] <= active_hub["ZD"] + tol and bar["close"] > active_hub["ZD"]
                    and bar["close"] > bar["open"] and body > cur_atr * 0.1):
                al = self._score_alignment("long", m_bias, w_bias, d_bias, h_bias,
                                           "hubBounceLong", daily_result, bar.get("time", 0))
                if al["score"] >= cfg["v14MinAlignScore"]:
                    return {
                        "bsPoint": {"type": "hubBounceLong", "price": bar["low"],
                                    "description": f"{label}中枢下沿反弹"},
                        "alignment": al, "direction": "long",
                    }

            if (bar["high"] >= active_hub["ZG"] - tol and bar["close"] < active_hub["ZG"]
                    and bar["close"] < bar["open"] and body > cur_atr * 0.1):
                al = self._score_alignment("short", m_bias, w_bias, d_bias, h_bias,
                                           "hubBounceShort", daily_result, bar.get("time", 0))
                if al["score"] >= cfg["v14MinAlignScore"]:
                    return {
                        "bsPoint": {"type": "hubBounceShort", "price": bar["high"],
                                    "description": f"{label}中枢上沿回落"},
                        "alignment": al, "direction": "short",
                    }

        return None

    # ------------------------------------------------------------------
    # Multi-TF alignment scoring
    # ------------------------------------------------------------------

    def _get_tf_bias(self, tf_result, current_time):
        if not tf_result:
            return "consolidation"
        bsp = tf_result.get("buySellPoints", [])
        if not bsp:
            return tf_result.get("currentTrend", "consolidation")

        b_types = {"buy1", "buy2", "buy3", "semiBuy2", "semiBuy3"}
        s_types = {"sell1", "sell2", "sell3", "semiSell2", "semiSell3"}
        last_buy_t = last_sell_t = 0

        for pt in bsp:
            pt_time = self._parse_ts(pt.get("date", ""))
            if pt_time > current_time:
                break
            if pt["type"] in b_types:
                last_buy_t = pt_time
            if pt["type"] in s_types:
                last_sell_t = pt_time

        if last_buy_t and last_sell_t:
            return "up" if last_buy_t > last_sell_t else "down"
        if last_buy_t:
            return "up"
        if last_sell_t:
            return "down"

        bis = tf_result.get("bis", [])
        if len(bis) >= 2:
            last_bi = bis[-1]
            ef = last_bi.get("endFractal")
            if ef:
                bi_time = self._parse_ts(ef.get("date", ""))
                if bi_time <= current_time:
                    return "up" if last_bi["direction"] == "up" else "down"

        return tf_result.get("currentTrend", "consolidation")

    def _score_alignment(self, direction, m_trend, w_trend, d_bias, h_bias,
                         signal_type, daily_result, current_time):
        score = 0
        reasons = []
        is_long = direction == "long"

        for trend, weight, name in [(m_trend, 15, "月线"), (w_trend, 12, "周线"),
                                     (d_bias, 12, "日线"), (h_bias, 10, "1h")]:
            align = (trend == "up") if is_long else (trend == "down")
            counter = (trend == "down") if is_long else (trend == "up")
            penalty = 5 if name == "月线" else 3 if name in ("周线", "日线") else 2
            neutral = 5
            if align:
                score += weight
                reasons.append(f'{name}{"上涨" if is_long else "下跌"}共振')
            elif counter:
                score -= penalty
                reasons.append(f'{name}{"下跌" if is_long else "上涨"}逆向')
            else:
                score += neutral
                reasons.append(f"{name}盘整")

        if daily_result and daily_result.get("buySellPoints"):
            check = {"buy1", "buy2", "buy3"} if is_long else {"sell1", "sell2", "sell3"}
            recent = [p for p in daily_result["buySellPoints"]
                      if p["type"] in check
                      and self._parse_ts(p.get("date", "")) <= current_time
                      and (current_time - self._parse_ts(p.get("date", ""))) < 15 * 86400000]
            if recent:
                score += 8
                reasons.append(f'日线近期有{"买" if is_long else "卖"}点信号')

        signal_scores = {
            "buy1": 25, "sell1": 25, "buy2": 20, "sell2": 20, "buy3": 15, "sell3": 15,
            "semiBuy2": 15, "semiSell2": 15, "semiBuy3": 12, "semiSell3": 12,
            "biReversalLong": 8, "biReversalShort": 8,
            "hubBounceLong": 10, "hubBounceShort": 10,
        }
        s_score = signal_scores.get(signal_type, 8)
        score += s_score
        sig_name = BUYSELL_LABELS.get(signal_type, {
            "biReversalLong": "笔底反转", "biReversalShort": "笔顶反转",
            "hubBounceLong": "中枢下沿反弹", "hubBounceShort": "中枢上沿回落",
        }.get(signal_type, signal_type))
        reasons.append(f"{sig_name}(+{s_score})")

        return {"score": score, "reasons": reasons, "valid": score >= 0}

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _open_position(self, bs_point, alignment, bar, index, cfg, cur_atr):
        rp = lambda v: self._rp(v, cfg)
        d = "long" if bs_point["type"] in LONG_TYPES else "short"
        stop_dist = max(rp(cur_atr * cfg["v14StopATR"]), rp(0.01))
        target_dist = max(rp(cur_atr * cfg["v14TargetATR"]), rp(stop_dist + 0.01))
        trail_dist = max(rp(cur_atr * cfg["v14TrailATR"]), rp(0.01))
        price = rp(bar["close"])
        label = BUYSELL_LABELS.get(bs_point["type"], bs_point.get("description", bs_point["type"]))

        reasons = alignment.get("reasons", [])
        trend_ctx = f"月{reasons[0][:4] if reasons else ''}/周{reasons[1][:4] if len(reasons) > 1 else ''}"

        breakout_types = {"buy3", "sell3", "semiBuy3", "semiSell3", "hubBounceLong", "hubBounceShort"}

        return {
            "direction": d,
            "entryPrice": price,
            "entryDate": bar["date"],
            "entryIndex": index,
            "entryContract": bar.get("contract", ""),
            "tradedContract": bar.get("contract", ""),
            "reason": f"{label} | {', '.join(reasons)}",
            "strategyType": f"v14:{bs_point['type']}",
            "signalFamily": label,
            "signalScore": alignment["score"],
            "trendContext": trend_ctx,
            "seasonPhase": "",
            "seasonCode": "",
            "timeSegment": self._get_time_segment(bar),
            "regime": "chanlun",
            "breakoutStyle": bs_point["type"] in breakout_types,
            "stopLoss": rp(price - stop_dist if d == "long" else price + stop_dist),
            "takeProfit": rp(price + target_dist if d == "long" else price - target_dist),
            "trailDistance": trail_dist,
            "trailActivation": rp(cur_atr * 1.0),
            "highestPrice": bar["high"],
            "lowestPrice": bar["low"],
            "closed": False,
            "exitPrice": 0,
            "exitReason": "",
            "isTrailing": False,
            "maxHoldBars": cfg["v14MaxHoldBars"],
            "entryAtr": cur_atr,
        }

    def _update_position(self, pos, bar, bar_idx, cfg, cur_atr):
        if cfg.get("t1Rule"):
            entry_day = (pos.get("entryDate") or "")[:10]
            current_day = (bar.get("date") or "")[:10]
            if entry_day and current_day and entry_day == current_day:
                pos["highestPrice"] = max(pos["highestPrice"], bar["high"])
                pos["lowestPrice"] = min(pos["lowestPrice"], bar["low"])
                return pos

        pos["highestPrice"] = max(pos["highestPrice"], bar["high"])
        pos["lowestPrice"] = min(pos["lowestPrice"], bar["low"])
        hold_bars = bar_idx - pos["entryIndex"]

        rp = lambda v: self._rp(v, cfg)

        if pos["maxHoldBars"] > 0 and hold_bars >= pos["maxHoldBars"]:
            pos["exitPrice"] = rp(bar["close"])
            pos["closed"] = True
            pos["exitReason"] = "超时平仓"
            return pos

        if pos["direction"] == "long":
            if bar["low"] <= pos["stopLoss"]:
                pos["exitPrice"] = pos["stopLoss"]
                pos["closed"] = True
                pos["exitReason"] = "止损"
                return pos
            if pos["trailDistance"] > 0:
                if not pos["isTrailing"] and (bar["high"] - pos["entryPrice"]) >= pos["trailActivation"]:
                    pos["isTrailing"] = True
                if pos["isTrailing"]:
                    ts = rp(pos["highestPrice"] - pos["trailDistance"])
                    if ts > pos["entryPrice"] and bar["close"] <= ts:
                        pos["exitPrice"] = ts
                        pos["closed"] = True
                        pos["exitReason"] = "跟踪止盈"
                        return pos
                    pos["stopLoss"] = max(pos["stopLoss"],
                                          rp(pos["highestPrice"] - pos["trailDistance"] * 1.5))
            if bar["high"] >= pos["takeProfit"]:
                pos["exitPrice"] = pos["takeProfit"]
                pos["closed"] = True
                pos["exitReason"] = "目标止盈"
                return pos
            if hold_bars >= 5:
                profit = bar["close"] - pos["entryPrice"]
                if profit > cur_atr * 0.5:
                    pos["stopLoss"] = rp(max(pos["stopLoss"], pos["entryPrice"] + profit * 0.3))
        else:
            if bar["high"] >= pos["stopLoss"]:
                pos["exitPrice"] = pos["stopLoss"]
                pos["closed"] = True
                pos["exitReason"] = "止损"
                return pos
            if pos["trailDistance"] > 0:
                if not pos["isTrailing"] and (pos["entryPrice"] - bar["low"]) >= pos["trailActivation"]:
                    pos["isTrailing"] = True
                if pos["isTrailing"]:
                    ts = rp(pos["lowestPrice"] + pos["trailDistance"])
                    if ts < pos["entryPrice"] and bar["close"] >= ts:
                        pos["exitPrice"] = ts
                        pos["closed"] = True
                        pos["exitReason"] = "跟踪止盈"
                        return pos
                    pos["stopLoss"] = min(pos["stopLoss"],
                                          rp(pos["lowestPrice"] + pos["trailDistance"] * 1.5))
            if bar["low"] <= pos["takeProfit"]:
                pos["exitPrice"] = pos["takeProfit"]
                pos["closed"] = True
                pos["exitReason"] = "目标止盈"
                return pos
            if hold_bars >= 5:
                profit = pos["entryPrice"] - bar["close"]
                if profit > cur_atr * 0.5:
                    pos["stopLoss"] = rp(min(pos["stopLoss"], pos["entryPrice"] - profit * 0.3))

        return pos

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------

    def _calc_atr(self, data, period):
        result = [0.0] * len(data)
        for i in range(1, len(data)):
            tr = max(data[i]["high"] - data[i]["low"],
                     abs(data[i]["high"] - data[i - 1]["close"]),
                     abs(data[i]["low"] - data[i - 1]["close"]))
            if i < period:
                result[i] = tr
            elif i == period:
                s = sum(max(data[j]["high"] - data[j]["low"],
                            abs(data[j]["high"] - data[j - 1]["close"]),
                            abs(data[j]["low"] - data[j - 1]["close"]))
                        for j in range(1, period + 1))
                result[i] = s / period
            else:
                result[i] = (result[i - 1] * (period - 1) + tr) / period
        return result

    def _get_time_segment(self, bar):
        date_str = bar.get("date", "")
        time_part = date_str[11:16] if len(date_str) > 15 else ""
        try:
            h = int(time_part[:2])
            m = int(time_part[3:5])
        except (ValueError, IndexError):
            return "其他"
        hhmm = h * 100 + m
        if 900 <= hhmm < 1015:
            return "早盘前段"
        if 1030 <= hhmm < 1130:
            return "早盘后段"
        if 1330 <= hhmm < 1430:
            return "午盘前段"
        if 1430 <= hhmm <= 1500:
            return "午盘尾盘"
        return "其他"

    # ------------------------------------------------------------------
    # PnL
    # ------------------------------------------------------------------

    def _calc_pnl(self, pos, cfg):
        diff = (pos["exitPrice"] - pos["entryPrice"] if pos["direction"] == "long"
                else pos["entryPrice"] - pos["exitPrice"])
        gross = diff * cfg["contractMultiplier"]

        comm_rate = cfg.get("commissionRate", 0)
        if comm_rate > 0:
            mult = cfg["contractMultiplier"]
            min_comm = cfg.get("minCommission", 5.0)
            buy_comm = max(pos["entryPrice"] * mult * comm_rate, min_comm)
            sell_comm = max(pos["exitPrice"] * mult * comm_rate, min_comm)
            stamp_tax = pos["exitPrice"] * mult * cfg.get("stampTaxRate", 0)
            return gross - buy_comm - sell_comm - stamp_tax

        return gross - cfg["commissionPerLot"] * 2

    def _trade_commission(self, trade, cfg):
        """Calculate total commission for a single trade."""
        comm_rate = cfg.get("commissionRate", 0)
        if comm_rate > 0:
            mult = cfg["contractMultiplier"]
            min_comm = cfg.get("minCommission", 5.0)
            buy_comm = max(trade["entryPrice"] * mult * comm_rate, min_comm)
            sell_comm = max(trade["exitPrice"] * mult * comm_rate, min_comm)
            stamp_tax = trade["exitPrice"] * mult * cfg.get("stampTaxRate", 0)
            return buy_comm + sell_comm + stamp_tax
        return cfg["commissionPerLot"] * 2

    def _total_commission(self, trades, cfg):
        return sum(self._trade_commission(t, cfg) for t in trades)

    def _avg_commission(self, trades, cfg):
        if not trades:
            return 0
        return self._total_commission(trades, cfg) / len(trades)

    def _unrealized_pnl(self, pos, bar, cfg):
        if not pos:
            return 0
        diff = (bar["close"] - pos["entryPrice"] if pos["direction"] == "long"
                else pos["entryPrice"] - bar["close"])
        gross = diff * cfg["contractMultiplier"]

        comm_rate = cfg.get("commissionRate", 0)
        if comm_rate > 0:
            mult = cfg["contractMultiplier"]
            min_comm = cfg.get("minCommission", 5.0)
            buy_comm = max(pos["entryPrice"] * mult * comm_rate, min_comm)
            sell_comm = max(bar["close"] * mult * comm_rate, min_comm)
            stamp_tax = bar["close"] * mult * cfg.get("stampTaxRate", 0)
            return gross - buy_comm - sell_comm - stamp_tax

        return gross

    # ------------------------------------------------------------------
    # Result builder
    # ------------------------------------------------------------------

    def _build_result(self, trades, signals, equity, cfg, data):
        total = len(trades)
        winners = [t for t in trades if t["pnl"] > 0]
        losers = [t for t in trades if t["pnl"] < 0]
        long_trades = [t for t in trades if t["direction"] == "long"]
        short_trades = [t for t in trades if t["direction"] == "short"]
        long_winners = [t for t in long_trades if t["pnl"] > 0]
        short_winners = [t for t in short_trades if t["pnl"] > 0]

        gross_profit = sum(t["pnl"] for t in winners)
        gross_loss = abs(sum(t["pnl"] for t in losers))
        net_profit = gross_profit - gross_loss
        win_rate = (len(winners) / total * 100) if total > 0 else 0
        avg_win = gross_profit / len(winners) if winners else 0
        avg_loss = gross_loss / len(losers) if losers else 0
        profit_factor = (gross_profit / gross_loss if gross_loss > 0
                         else (float("inf") if gross_profit > 0 else 0))

        peak = cfg["initialCapital"]
        max_equity = cfg["initialCapital"]
        max_dd = max_dd_pct = 0.0
        for e in equity:
            v = e["value"]
            if v > max_equity:
                max_equity = v
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (dd / peak) * 100 if peak else 0

        first_time = data[0].get("time", 0)
        last_time = data[-1].get("time", 0)
        span_days = max(1, (last_time - first_time) / 86400000)
        span_years = span_days / 365
        annual_return = (net_profit / cfg["initialCapital"] / span_years) * 100 if span_years > 0 else 0
        sharpe = self._calc_sharpe(equity, cfg["initialCapital"])

        def _bucket(trades_list, key_fn):
            m: dict = {}
            for t in trades_list:
                k = key_fn(t)
                if k not in m:
                    m[k] = {"count": 0, "wins": 0, "pnl": 0.0}
                m[k]["count"] += 1
                if t["pnl"] > 0:
                    m[k]["wins"] += 1
                m[k]["pnl"] += t["pnl"]
            return m

        by_reason = _bucket(trades, lambda t: t.get("reason", ""))
        by_month = _bucket(trades, lambda t: (t.get("entryDate") or "")[:7] or "未知")
        by_strategy = _bucket(trades, lambda t: t.get("strategyType", "v14:unknown"))
        by_signal_family = _bucket(trades, lambda t: t.get("signalFamily", "未分类"))
        by_time_segment = _bucket(trades, lambda t: self._get_time_segment({"date": t.get("entryDate", "")}))
        by_season_phase = _bucket(trades, lambda t: t.get("seasonPhase") or "未知阶段")
        by_trend_context = _bucket(trades, lambda t: t.get("trendContext") or "未知环境")

        long_pnl = sum(t["pnl"] for t in long_trades)
        short_pnl = sum(t["pnl"] for t in short_trades)
        direction_stats = {
            "long": {
                "count": len(long_trades),
                "wins": len(long_winners),
                "winRate": (len(long_winners) / len(long_trades) * 100) if long_trades else 0,
                "pnl": long_pnl,
                "avgPnl": long_pnl / len(long_trades) if long_trades else 0,
            },
            "short": {
                "count": len(short_trades),
                "wins": len(short_winners),
                "winRate": (len(short_winners) / len(short_trades) * 100) if short_trades else 0,
                "pnl": short_pnl,
                "avgPnl": short_pnl / len(short_trades) if short_trades else 0,
            },
        }

        v14_preset_labels = {
            "balanced": "均衡",
            "conservative": "保守",
            "aggressive": "积极",
        }
        preset_key = cfg.get("v14Preset", "balanced")
        preset_label = v14_preset_labels.get(preset_key, preset_key)

        return {
            "summary": {
                "initialCapital": cfg["initialCapital"],
                "finalCapital": cfg["initialCapital"] + net_profit,
                "netProfit": net_profit,
                "netProfitPct": (net_profit / cfg["initialCapital"]) * 100,
                "totalTrades": total,
                "winners": len(winners),
                "losers": len(losers),
                "winRate": win_rate,
                "grossProfit": gross_profit,
                "grossLoss": gross_loss,
                "profitFactor": profit_factor,
                "avgWin": avg_win,
                "avgLoss": avg_loss,
                "maxDrawdown": max_dd,
                "maxDrawdownPct": max_dd_pct,
                "maxEquity": max_equity,
                "annualReturn": annual_return,
                "sharpeRatio": sharpe,
                "longTrades": len(long_trades),
                "shortTrades": len(short_trades),
                "longWinRate": (len(long_winners) / len(long_trades) * 100) if long_trades else 0,
                "shortWinRate": (len(short_winners) / len(short_trades) * 100) if short_trades else 0,
                "totalCommission": self._total_commission(trades, cfg),
                "commissionPerTrade": self._avg_commission(trades, cfg),
                "strategyName": f"多周期缠论V14 - {preset_label}",
            },
            "byReason": by_reason,
            "byMonth": by_month,
            "byStrategy": by_strategy,
            "bySignalFamily": by_signal_family,
            "byTimeSegment": by_time_segment,
            "directionStats": direction_stats,
            "bySeasonPhase": by_season_phase,
            "byTrendContext": by_trend_context,
            "byRegime": self._calc_by_regime(trades),
            "dailyTradeDistribution": self._calc_daily_trade_dist(trades),
            "trades": trades,
            "signals": signals,
            "equity": equity,
            "params": cfg,
        }

    def _calc_sharpe(self, equity, initial_capital):
        if not equity or len(equity) < 2:
            return 0.0
        daily_map: dict[str, float] = {}
        for e in equity:
            day = (e.get("date") or "")[:10]
            if day:
                daily_map[day] = e["value"]
        vals = list(daily_map.values())
        if len(vals) < 2:
            return 0.0
        returns = []
        for i in range(1, len(vals)):
            if vals[i - 1] == 0:
                continue
            returns.append((vals[i] - vals[i - 1]) / vals[i - 1])
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    @staticmethod
    def _calc_by_regime(trades):
        by_regime: dict = {}
        for t in trades:
            key = t.get("regime") or t.get("trendContext") or "未知"
            if key not in by_regime:
                by_regime[key] = {"count": 0, "wins": 0, "pnl": 0.0}
            by_regime[key]["count"] += 1
            if t["pnl"] > 0:
                by_regime[key]["wins"] += 1
            by_regime[key]["pnl"] += t["pnl"]
        return by_regime

    @staticmethod
    def _calc_daily_trade_dist(trades):
        by_day: dict[str, int] = {}
        for t in trades:
            day = (t.get("entryDate") or "")[:10]
            if not day:
                continue
            by_day[day] = by_day.get(day, 0) + 1
        counts = list(by_day.values())
        if not counts:
            return {"avg": 0, "min": 0, "max": 0, "tradingDays": 0, "distribution": {}}
        avg = sum(counts) / len(counts)
        dist: dict[str, int] = {}
        for c in counts:
            key = "6+" if c >= 6 else str(c)
            dist[key] = dist.get(key, 0) + 1
        return {
            "avg": round(avg * 100) / 100,
            "min": min(counts),
            "max": max(counts),
            "tradingDays": len(counts),
            "distribution": dist,
        }

    def _empty_result(self, msg=""):
        return {
            "summary": None,
            "byReason": {},
            "byMonth": {},
            "byTimeSegment": {},
            "directionStats": {"long": {}, "short": {}},
            "byStrategy": {},
            "bySignalFamily": {},
            "bySeasonPhase": {},
            "byTrendContext": {},
            "byRegime": {},
            "dailyTradeDistribution": {"avg": 0, "min": 0, "max": 0, "tradingDays": 0, "distribution": {}},
            "trades": [],
            "signals": [],
            "equity": [],
            "params": {},
            "error": msg,
        }

    @staticmethod
    def _parse_ts(date_str) -> int:
        if not date_str:
            return 0
        try:
            return int(datetime.fromisoformat(str(date_str).replace(" ", "T")).timestamp() * 1000)
        except Exception:
            return 0
