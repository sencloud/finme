"""Mechanical execution layer: Chanlun multi-TF direction filter + price rules.

Rules (priority order): hub_breakout, bi_extreme, hub_pullback.
Output signals are compatible with :class:`AutoOrderManager.process_signals`.
"""

from __future__ import annotations

from datetime import datetime


class ExecutionEngine:
    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.enabled = cfg.get("enabled", True)
        self.min_atr_bar_ratio = float(cfg.get("min_atr_bar_ratio", 0.3))
        self.max_sl_atr = float(cfg.get("max_sl_atr", 2.0))
        self.hub_tolerance_atr = float(cfg.get("hub_tolerance_atr", 0.5))
        self.target_atr = float(cfg.get("target_atr", 3.0))
        self.min_alignment_tfs = int(cfg.get("min_alignment_tfs", 2))
        self.rules: list[str] = list(
            cfg.get("rules") or ["hub_breakout", "bi_extreme", "hub_pullback"]
        )

    def evaluate(self, scan_result: dict) -> list[dict]:
        if not self.enabled:
            return []
        out: list[dict] = []
        for row in scan_result.get("results") or []:
            ctx = row.get("structuralContext")
            if not ctx:
                continue
            bias = self._compute_direction_bias(ctx)
            if bias == "neutral":
                continue
            sig = None
            for rule in self.rules:
                if rule == "hub_breakout":
                    sig = self._rule_hub_breakout(ctx, bias)
                elif rule == "bi_extreme":
                    sig = self._rule_bi_extreme(ctx, bias)
                elif rule == "hub_pullback":
                    sig = self._rule_hub_pullback(ctx, bias)
                else:
                    continue
                if sig and self._passes_filters(sig, ctx):
                    out.append(sig)
                    break
        return out

    def _compute_direction_bias(self, ctx: dict) -> str:
        trends = [
            ctx.get("direction"),
            ctx.get("structureDirection"),
            ctx.get("entryDirection"),
        ]
        long_c = sum(1 for t in trends if t == "up")
        short_c = sum(1 for t in trends if t == "down")
        need = self.min_alignment_tfs
        if long_c >= need and long_c >= short_c:
            return "long"
        if short_c >= need and short_c >= long_c:
            return "short"
        return "neutral"

    def _rule_hub_breakout(self, ctx: dict, bias: str) -> dict | None:
        hub = ctx.get("lastHub")
        bars = ctx.get("recentBars") or []
        atr = float(ctx.get("atr") or 0)
        if not hub or len(bars) < 2 or atr <= 0:
            return None
        zg = float(hub["ZG"])
        zd = float(hub["ZD"])
        c0 = float(bars[-2]["close"])
        c1 = float(bars[-1]["close"])
        last_date = bars[-1].get("date", "")

        if bias == "long" and c1 > zg and c0 <= zg:
            entry = c1
            sl = zg - 0.5 * atr
            tp = entry + self.target_atr * atr
            return self._build_signal(ctx, "hub_breakout", "exec_hub_breakout",
                                      "long", entry, sl, tp, last_date,
                                      {"hubZG": zg, "hubZD": zd})
        if bias == "short" and c1 < zd and c0 >= zd:
            entry = c1
            sl = zd + 0.5 * atr
            tp = entry - self.target_atr * atr
            return self._build_signal(ctx, "hub_breakout", "exec_hub_breakout",
                                      "short", entry, sl, tp, last_date,
                                      {"hubZG": zg, "hubZD": zd})
        return None

    def _rule_bi_extreme(self, ctx: dict, bias: str) -> dict | None:
        prev_bi = ctx.get("prevBi")
        last_bi = ctx.get("lastBi")
        bars = ctx.get("recentBars") or []
        atr = float(ctx.get("atr") or 0)
        if not prev_bi or not last_bi or not bars or atr <= 0:
            return None
        if not prev_bi.get("finished") or not last_bi.get("finished"):
            return None
        close = float(bars[-1]["close"])
        last_date = bars[-1].get("date", "")
        pdir = prev_bi.get("direction")
        ph = float(prev_bi["high"])
        pl = float(prev_bi["low"])
        lh = float(last_bi["high"])
        ll = float(last_bi["low"])

        if bias == "long" and pdir == "down" and close > ph:
            entry = close
            sl = ll
            tp = entry + self.target_atr * atr
            return self._build_signal(ctx, "bi_extreme", "exec_bi_extreme",
                                      "long", entry, sl, tp, last_date,
                                      {"prevBiHigh": ph, "prevBiLow": pl,
                                       "lastBiHigh": lh, "lastBiLow": ll})
        if bias == "short" and pdir == "up" and close < pl:
            entry = close
            sl = lh
            tp = entry - self.target_atr * atr
            return self._build_signal(ctx, "bi_extreme", "exec_bi_extreme",
                                      "short", entry, sl, tp, last_date,
                                      {"prevBiHigh": ph, "prevBiLow": pl,
                                       "lastBiHigh": lh, "lastBiLow": ll})
        return None

    def _rule_hub_pullback(self, ctx: dict, bias: str) -> dict | None:
        hub = ctx.get("lastHub")
        bars = ctx.get("recentBars") or []
        atr = float(ctx.get("atr") or 0)
        if not hub or not bars or atr <= 0:
            return None
        zg = float(hub["ZG"])
        zd = float(hub["ZD"])
        tol = self.hub_tolerance_atr * atr
        close = float(bars[-1]["close"])
        last_date = bars[-1].get("date", "")

        if bias == "long":
            lo = zg - tol
            hi = zg + tol
            if lo <= close <= hi:
                entry = close
                sl = zd
                tp = entry + self.target_atr * atr
                return self._build_signal(ctx, "hub_pullback", "exec_hub_pullback",
                                          "long", entry, sl, tp, last_date,
                                          {"hubZG": zg, "hubZD": zd})
        elif bias == "short":
            lo = zd - tol
            hi = zd + tol
            if lo <= close <= hi:
                entry = close
                sl = zg
                tp = entry - self.target_atr * atr
                return self._build_signal(ctx, "hub_pullback", "exec_hub_pullback",
                                          "short", entry, sl, tp, last_date,
                                          {"hubZG": zg, "hubZD": zd})
        return None

    def _passes_filters(self, sig: dict, ctx: dict) -> bool:
        bars = ctx.get("recentBars") or []
        atr = float(ctx.get("atr") or 0)
        if not bars or atr <= 0:
            return False
        last = bars[-1]
        rng = float(last["high"]) - float(last["low"])
        if rng < self.min_atr_bar_ratio * atr:
            return False
        entry = float(sig["entryPrice"])
        sl = float(sig["stopLoss"])
        risk = abs(entry - sl)
        if risk <= 0 or risk > self.max_sl_atr * atr:
            return False
        return True

    def _build_signal(
        self,
        ctx: dict,
        rule: str,
        sig_type: str,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        bar_date: str,
        trigger_levels: dict,
    ) -> dict:
        prefix = ctx.get("varietyCode", "")
        display = ctx.get("displayName", "")
        ts_code = ctx.get("executionCode") or ""
        safe_date = str(bar_date).replace(" ", "-").replace(":", "")[:20]
        sig_id = f"exec_{prefix}_{rule}_{safe_date}"

        rr = 0.0
        risk = abs(entry - sl)
        if risk > 0:
            rr = round(abs(tp - entry) / risk, 2)

        score_map = {"hub_breakout": 78, "bi_extreme": 80, "hub_pullback": 74}
        score = score_map.get(rule, 72)
        conf = "high" if rule != "hub_pullback" else "medium"

        return {
            "id": sig_id,
            "source": "execution",
            "rule": rule,
            "varietyCode": prefix,
            "displayName": display,
            "trendSeriesCode": f"{prefix}.{ctx.get('exchange', '')}",
            "executionTsCode": ts_code,
            "type": sig_type,
            "direction": direction,
            "price": entry,
            "entryPrice": round(entry, 2),
            "stopLoss": round(sl, 2),
            "takeProfit": round(tp, 2),
            "riskRewardRatio": rr,
            "date": bar_date,
            "timeframe": "15m",
            "seriesType": "execution",
            "finished": True,
            "biFinished": True,
            # Execution-layer signals use mechanical confirmation (price rules),
            # not Chanlun structural confirmation (bi-finished). Mark confirmed
            # so they appear in history, but source="execution" distinguishes
            # them from Chanlun BSP signals for trade gating.
            "confirmed": True,
            "tradeable": True,
            "confidence": conf,
            "compositeScore": score,
            "v14AlignScore": None,
            "trendContext": "执行层/机械入场",
            "triggerLevels": trigger_levels,
            "status": "pending",
            "createdAt": datetime.now().isoformat(),
        }
