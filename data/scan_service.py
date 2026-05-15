"""Background scanning service for futures varieties.

Ported from main/services/scan-service.js.
Triple-track Chanlun analysis: trend (1d), structure (1h), entry (15m).
Enhanced with V14-style multi-TF alignment scoring for signal quality.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from ..core.analyzer import ChanlunAnalyzer
from ..core.types import BUYSELL_LABELS
from ..strategy.signal_rules import (
    apply_signal_rules,
    merge_signals,
    merge_signal_events,
    compute_atr,
)

logger = logging.getLogger(__name__)

_CTP_TO_TUSHARE_EXCHANGE = {"SHFE": "SHF", "CZCE": "ZCE", "CFFEX": "CFE"}


def to_tushare_exchange(ctp_exchange: str) -> str:
    """Convert CTP exchange code to Tushare exchange suffix.

    CTP uses SHFE / CZCE / CFFEX whereas Tushare uses SHF / ZCE / CFE.
    DCE and INE are the same in both systems.
    """
    return _CTP_TO_TUSHARE_EXCHANGE.get(ctp_exchange, ctp_exchange)


def _hub_zone(hub: dict | None) -> dict | None:
    if not hub:
        return None
    out = {}
    for k in ("ZG", "ZD", "GG", "DD"):
        if k in hub:
            out[k] = hub[k]
    return out if out else None


def _bi_snapshot(bi: dict | None) -> dict | None:
    if not bi:
        return None
    return {
        "direction": bi.get("direction"),
        "high": bi.get("high"),
        "low": bi.get("low"),
        "finished": bi.get("finished", True),
    }


def _build_structural_context(
    prefix: str,
    exchange: str,
    display_name: str,
    execution_code: str | None,
    trend_result: dict,
    structure_result: dict | None,
    entry_result: dict | None,
) -> dict | None:
    """Extract hub/bi/price context for the mechanical execution layer."""
    if not entry_result or not entry_result.get("mergedKlines"):
        return None

    mk = entry_result["mergedKlines"]
    hubs = entry_result.get("hubs") or []
    bis = entry_result.get("bis") or []
    last_hub = _hub_zone(hubs[-1]) if hubs else None
    struct_hubs = (structure_result or {}).get("hubs") or []
    structure_hub = _hub_zone(struct_hubs[-1]) if struct_hubs else None

    last_bi = _bi_snapshot(bis[-1]) if bis else None
    prev_bi = _bi_snapshot(bis[-2]) if len(bis) >= 2 else None

    n = min(5, len(mk))
    recent = mk[-n:]
    recent_bars = [
        {
            "open": b.get("open"),
            "high": b.get("high"),
            "low": b.get("low"),
            "close": b.get("close"),
            "date": b.get("date"),
        }
        for b in recent
    ]
    last_bar = mk[-1]
    atr = compute_atr(mk, 14)

    return {
        "varietyCode": prefix,
        "displayName": display_name,
        "exchange": exchange,
        "executionCode": execution_code or "",
        "direction": trend_result.get("currentTrend"),
        "structureDirection": structure_result.get("currentTrend") if structure_result else None,
        "entryDirection": entry_result.get("currentTrend"),
        "lastHub": last_hub,
        "structureHub": structure_hub,
        "lastBi": last_bi,
        "prevBi": prev_bi,
        "recentBars": recent_bars,
        "lastPrice": last_bar.get("close"),
        "atr": atr,
    }


EXCHANGE_MAP = {
    # 农产品
    "C": "DCE", "CS": "DCE", "M": "DCE", "Y": "DCE", "P": "DCE",
    "A": "DCE", "JD": "DCE", "OI": "ZCE", "RM": "ZCE", "CF": "ZCE",
    "SR": "ZCE",
    # 黑色
    "I": "DCE", "J": "DCE", "JM": "DCE", "RB": "SHFE", "HC": "SHFE",
    "SF": "ZCE", "SM": "ZCE", "SS": "SHFE",
    # 化工
    "L": "DCE", "V": "DCE", "PP": "DCE", "EG": "DCE", "EB": "DCE",
    "TA": "ZCE", "MA": "ZCE", "SA": "ZCE", "FG": "ZCE", "UR": "ZCE",
    "BU": "SHFE", "RU": "SHFE", "PG": "DCE", "SC": "INE",
    # 有色 / 贵金属
    "CU": "SHFE", "AL": "SHFE", "ZN": "SHFE", "NI": "SHFE",
    "AU": "SHFE", "AG": "SHFE",
}

FUTURES_NAMES = {
    # 农产品
    "C": "玉米", "CS": "淀粉", "M": "豆粕", "Y": "豆油", "P": "棕榈油",
    "A": "豆一", "JD": "鸡蛋", "OI": "菜油", "RM": "菜粕", "CF": "棉花",
    "SR": "白糖",
    # 黑色
    "I": "铁矿石", "J": "焦炭", "JM": "焦煤", "RB": "螺纹钢", "HC": "热卷",
    "SF": "硅铁", "SM": "锰硅", "SS": "不锈钢",
    # 化工
    "L": "塑料", "V": "PVC", "PP": "聚丙烯", "EG": "乙二醇", "EB": "苯乙烯",
    "TA": "PTA", "MA": "甲醇", "SA": "纯碱", "FG": "玻璃", "UR": "尿素",
    "BU": "沥青", "RU": "橡胶", "PG": "LPG", "SC": "原油",
    # 有色 / 贵金属
    "CU": "铜", "AL": "铝", "ZN": "锌", "NI": "镍",
    "AU": "黄金", "AG": "白银",
}


class ScanService:
    def __init__(self, tushare_service, signal_store: list[dict] | None = None,
                 realtime_source=None) -> None:
        self.tushare = tushare_service
        self.realtime = realtime_source
        self.signal_store = signal_store if signal_store is not None else []
        self.signal_events: list[dict] = []
        self._analyzer = ChanlunAnalyzer()
        self._scanning = False
        self._progress = {"current": 0, "total": 0, "currentPrefix": ""}
        self._last_results: dict | None = None

    async def scan_all(self, prefixes: list[str] | None = None,
                       options: dict | None = None) -> dict:
        return await asyncio.to_thread(self.scan_all_sync, prefixes, options)

    def scan_all_sync(self, prefixes: list[str] | None = None,
                      options: dict | None = None) -> dict:
        if self._scanning:
            return {"error": "扫描正在进行中", "progress": self._progress}

        self._scanning = True
        opts = options or {}
        results = []
        all_signals: list[dict] = []

        try:
            scan_prefixes = prefixes or list(EXCHANGE_MAP.keys())
            self._progress = {"current": 0, "total": len(scan_prefixes), "currentPrefix": ""}

            for prefix in scan_prefixes:
                self._progress["currentPrefix"] = prefix
                try:
                    result = self._scan_variety(prefix, opts)
                    if result:
                        results.append(result)
                        if result.get("signals"):
                            all_signals.extend(result["signals"])
                except Exception as e:
                    logger.error("[ScanService] 品种 %s 扫描失败: %s", prefix, e)
                self._progress["current"] += 1

            if all_signals:
                self.signal_store[:] = merge_signals(self.signal_store, all_signals)
                if len(self.signal_store) > 1000:
                    self.signal_store[:] = self.signal_store[-1000:]

                self.signal_events = merge_signal_events(self.signal_events, all_signals)
                if len(self.signal_events) > 5000:
                    self.signal_events = self.signal_events[-5000:]

            self._last_results = {
                "results": results,
                "signals": all_signals,
                "scannedAt": datetime.now().isoformat(),
                "varietyCount": len(scan_prefixes),
                "signalCount": len(all_signals),
            }
            return self._last_results
        finally:
            self._scanning = False

    def _scan_variety(self, prefix: str, options: dict) -> dict | None:
        exchange = EXCHANGE_MAP.get(prefix)
        if not exchange:
            return None

        ts_exchange = to_tushare_exchange(exchange)
        trend_code = f"{prefix}.{ts_exchange}"
        display_name = FUTURES_NAMES.get(prefix, prefix)

        mapping = self.tushare.resolve_execution_contract(trend_code)
        execution_code = mapping.get("executionTsCode")

        end_date = self._format_date(date.today())
        start_date_1d = self._get_start_date("1d")
        start_date_1w = self._get_start_date("1w")

        try:
            trend_daily_raw = self.tushare.get_futures_daily(trend_code, start_date_1d, end_date)
        except Exception as e:
            logger.warning("[ScanService] %s 主力日线拉取失败: %s", prefix, e)
            return None

        if not trend_daily_raw or len(trend_daily_raw) < 30:
            logger.warning("[ScanService] %s 主力日线数据不足", prefix)
            return None

        trend_klines = self._transform_daily(trend_daily_raw)
        trend_result = self._analyzer.analyze(trend_klines)
        weekly_result = None

        structure_result = None
        entry_result = None
        weekly_klines: list[dict] = []
        h1_klines: list[dict] = []
        m15_klines: list[dict] = []

        try:
            weekly_raw = self.tushare.get_futures_weekly(trend_code, start_date_1w, end_date)
            weekly_klines = self._transform_daily(weekly_raw) if weekly_raw else []
            if weekly_klines and len(weekly_klines) >= 10:
                weekly_result = self._analyzer.analyze(weekly_klines)
        except Exception as e:
            logger.warning("[ScanService] %s 主力周线拉取失败: %s", prefix, e)

        if execution_code:
            struct_raw = self._fetch_minutes(prefix, execution_code, "60min", end_date)
            if struct_raw and len(struct_raw) >= 30:
                h1_klines = self._transform_minute(struct_raw)
                structure_result = self._analyzer.analyze(h1_klines)

            entry_raw = self._fetch_minutes(prefix, execution_code, "15min", end_date)
            if entry_raw and len(entry_raw) >= 30:
                m15_klines = self._transform_minute(entry_raw)
                entry_result = self._analyzer.analyze(m15_klines)

        signals = apply_signal_rules({
            "prefix": prefix, "exchange": exchange, "displayName": display_name,
            "trendCode": trend_code, "executionCode": execution_code,
            "trendResult": trend_result, "structureResult": structure_result,
            "entryResult": entry_result, "mappingDate": mapping.get("mappingDate"),
        }, {
            "recentBars": options.get("recentBars", 5),
            "requireFinished": options.get("requireFinished", True),
            "requireTrendAlignment": options.get("requireTrendAlignment", False),
            "includePartialTypes": options.get("includePartialTypes", False),
        })

        min_align = options.get("v14MinAlignScore", 25)
        if min_align > 0:
            signals = self._apply_v14_alignment(
                signals, trend_result, structure_result, entry_result, min_align)

        signals = self._keep_latest_per_direction(signals)

        last_price = None
        if entry_result and entry_result.get("mergedKlines"):
            last_bar = entry_result["mergedKlines"][-1]
            last_price = last_bar.get("close")

        structural_context = _build_structural_context(
            prefix, exchange, display_name, execution_code,
            trend_result, structure_result, entry_result,
        )

        multi_period = {
            "1d": {"result": trend_result},
        }
        if weekly_result:
            multi_period["1w"] = {"result": weekly_result}
        if structure_result:
            multi_period["1h"] = {"result": structure_result}
        if entry_result:
            multi_period["15m"] = {"result": entry_result}

        return {
            "prefix": prefix, "exchange": exchange, "displayName": display_name,
            "trendCode": trend_code, "executionCode": execution_code,
            "lastPrice": last_price,
            "multiPeriod": multi_period,
            "timeframeBars": {
                "1d": trend_klines,
                "1w": weekly_klines,
                "1h": h1_klines,
                "15m": m15_klines,
            },
            "structuralContext": structural_context,
            "trend": {
                "direction": trend_result["currentTrend"],
                "movementType": trend_result["movementType"],
                "hubCount": len(trend_result["hubs"]),
                "biCount": len(trend_result["bis"]),
                "signalCount": len(trend_result["buySellPoints"]),
                "completeness": trend_result.get("completeness"),
            },
            "structure": {
                "direction": structure_result["currentTrend"],
                "movementType": structure_result["movementType"],
                "hubCount": len(structure_result["hubs"]),
                "biCount": len(structure_result["bis"]),
                "signalCount": len(structure_result["buySellPoints"]),
            } if structure_result else None,
            "entry": {
                "direction": entry_result["currentTrend"],
                "movementType": entry_result["movementType"],
                "hubCount": len(entry_result["hubs"]),
                "biCount": len(entry_result["bis"]),
                "signalCount": len(entry_result["buySellPoints"]),
            } if entry_result else None,
            "signals": signals,
            "scannedAt": datetime.now().isoformat(),
        }

    def _fetch_minutes(self, prefix: str, ts_code: str,
                       freq: str, end_date: str) -> list[dict]:
        """Fetch minute bars, preferring AkShare realtime then Tushare cache.

        In live mode, AkShare provides real-time Sina data. Tushare historical
        cache is merged as base to ensure enough bars for Chanlun analysis.
        """
        tf_key = "1h" if "60" in freq else "15m"
        start_date = self._get_start_date(tf_key)

        realtime_bars: list[dict] = []
        if self.realtime:
            try:
                realtime_bars = self.realtime.get_realtime_minutes(ts_code, freq)
            except Exception as e:
                logger.warning("[ScanService] %s AkShare %s 失败: %s, 回退Tushare", prefix, freq, e)

        if realtime_bars and len(realtime_bars) >= 30:
            logger.info("[ScanService] %s %s: AkShare实时 %d根K线", prefix, freq, len(realtime_bars))
            tushare_bars = self._fetch_tushare_minutes_safe(prefix, ts_code, freq, start_date, end_date)
            if tushare_bars:
                return self._merge_minute_bars(tushare_bars, realtime_bars)
            return realtime_bars

        logger.info("[ScanService] %s %s: ts_code=%s, %s~%s (Tushare)",
                    prefix, freq, ts_code, start_date, end_date)
        return self._fetch_tushare_minutes_safe(prefix, ts_code, freq, start_date, end_date)

    def _fetch_tushare_minutes_safe(self, prefix: str, ts_code: str,
                                     freq: str, start_date: str,
                                     end_date: str) -> list[dict]:
        try:
            return self.tushare.get_futures_minutes(ts_code, freq, start_date, end_date) or []
        except Exception as e:
            logger.warning("[ScanService] %s Tushare %s 拉取失败 (%s): %s", prefix, freq, ts_code, e)
            return []

    @staticmethod
    def _merge_minute_bars(base: list[dict], realtime: list[dict]) -> list[dict]:
        """Merge Tushare historical bars with AkShare realtime bars.

        AkShare bars override Tushare bars for the same timestamp.
        This gives us enough history for analysis plus fresh live data.
        """
        def _bar_key(bar: dict) -> str:
            return str(bar.get("trade_time", bar.get("datetime", "")))[:16]

        merged: dict[str, dict] = {}
        for b in base:
            k = _bar_key(b)
            if k:
                merged[k] = b
        for b in realtime:
            k = _bar_key(b)
            if k:
                merged[k] = b
        return sorted(merged.values(), key=lambda x: _bar_key(x))

    @staticmethod
    def _keep_latest_per_direction(signals: list[dict]) -> list[dict]:
        """Keep only the most recent signal per direction (long/short).

        For each direction, pick the signal with the latest date so that
        the output shows at most one long candidate and one short candidate.
        """
        latest: dict[str, dict] = {}
        for sig in signals:
            key = sig.get("direction", "")
            existing = latest.get(key)
            if existing is None or sig.get("date", "") > existing.get("date", ""):
                latest[key] = sig
        return list(latest.values())

    # ------------------------------------------------------------------
    # V14 multi-TF alignment scoring
    # ------------------------------------------------------------------

    def _apply_v14_alignment(self, signals: list[dict],
                              trend_result, structure_result, entry_result,
                              min_align_score: int) -> list[dict]:
        """Score each signal using V14-style multi-TF alignment and filter."""
        d_bias = self._get_tf_bias(trend_result)
        h_bias = self._get_tf_bias(structure_result)

        filtered = []
        for sig in signals:
            direction = sig.get("direction", "")
            signal_type = sig.get("type", "")
            is_long = direction == "long"

            score = 0
            reasons = []

            for trend, weight, name in [(d_bias, 12, "日线"), (h_bias, 10, "1h")]:
                align = (trend == "up") if is_long else (trend == "down")
                counter = (trend == "down") if is_long else (trend == "up")
                penalty = 3 if name == "日线" else 2
                if align:
                    score += weight
                    reasons.append(f'{name}{"上涨" if is_long else "下跌"}共振')
                elif counter:
                    score -= penalty
                    reasons.append(f'{name}{"下跌" if is_long else "上涨"}逆向')
                else:
                    score += 5
                    reasons.append(f"{name}盘整")

            if trend_result and trend_result.get("buySellPoints"):
                check = {"buy1", "buy2", "buy3"} if is_long else {"sell1", "sell2", "sell3"}
                recent = [p for p in trend_result["buySellPoints"] if p["type"] in check]
                if recent:
                    score += 8
                    reasons.append(f'日线近期有{"买" if is_long else "卖"}点信号')

            signal_scores = {
                "buy1": 25, "sell1": 25, "buy2": 20, "sell2": 20,
                "buy3": 15, "sell3": 15, "semiBuy2": 15, "semiSell2": 15,
                "semiBuy3": 12, "semiSell3": 12,
                "shadowLong": 10, "shadowShort": 10,
            }
            s_score = signal_scores.get(signal_type, 8)
            score += s_score
            sig_name = BUYSELL_LABELS.get(signal_type, signal_type)
            reasons.append(f"{sig_name}(+{s_score})")

            sig["v14AlignScore"] = score
            sig["v14AlignReasons"] = reasons

            if score >= min_align_score:
                filtered.append(sig)
            else:
                logger.debug("[ScanService] 信号 %s 对齐分低于阈值: %d < %d",
                             sig.get("id", ""), score, min_align_score)

        return filtered

    @staticmethod
    def _get_tf_bias(tf_result) -> str:
        if not tf_result:
            return "consolidation"
        bsp = tf_result.get("buySellPoints", [])
        if not bsp:
            return tf_result.get("currentTrend", "consolidation")

        b_types = {"buy1", "buy2", "buy3", "semiBuy2", "semiBuy3"}
        s_types = {"sell1", "sell2", "sell3", "semiSell2", "semiSell3"}
        last_buy = last_sell = None
        for pt in bsp:
            if pt["type"] in b_types:
                last_buy = pt
            if pt["type"] in s_types:
                last_sell = pt

        if last_buy and last_sell:
            return "up" if (last_buy.get("date", "") >= last_sell.get("date", "")) else "down"
        if last_buy:
            return "up"
        if last_sell:
            return "down"

        bis = tf_result.get("bis", [])
        if len(bis) >= 2:
            return "up" if bis[-1].get("direction") == "up" else "down"

        return tf_result.get("currentTrend", "consolidation")

    # ------------------------------------------------------------------
    # Data transforms
    # ------------------------------------------------------------------

    @staticmethod
    def _transform_daily(raw: list[dict]) -> list[dict]:
        result = []
        for item in raw:
            ds = str(item.get("trade_date", ""))
            try:
                o = float(item["open"])
                h = float(item["high"])
                lo = float(item["low"])
                c = float(item["close"])
            except (KeyError, ValueError, TypeError):
                continue
            vol = float(item.get("vol") or item.get("volume") or 0)
            formatted = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}" if len(ds) >= 8 else ds
            ts = int(datetime.strptime(formatted, "%Y-%m-%d").timestamp() * 1000) if formatted else 0
            result.append({"time": ts, "date": formatted, "open": o, "high": h, "low": lo, "close": c, "volume": vol})
        result.sort(key=lambda x: x["time"])
        return result

    @staticmethod
    def _transform_minute(raw: list[dict]) -> list[dict]:
        result = []
        for item in raw:
            tv = str(item.get("trade_time", ""))
            try:
                o = float(item["open"])
                h = float(item["high"])
                lo = float(item["low"])
                c = float(item["close"])
            except (KeyError, ValueError, TypeError):
                continue
            vol = float(item.get("vol") or item.get("volume") or 0)
            try:
                dt = datetime.fromisoformat(tv.replace("/", "-"))
                ts = int(dt.timestamp() * 1000)
                date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = 0
                date_str = tv
            result.append({"time": ts, "date": date_str, "open": o, "high": h, "low": lo, "close": c, "volume": vol})
        result.sort(key=lambda x: x["time"])
        return result

    @staticmethod
    def _format_date(d: date) -> str:
        return d.strftime("%Y%m%d")

    @staticmethod
    def _get_start_date(timeframe: str) -> str:
        now = date.today()
        offsets = {"1d": 365, "1w": 3 * 365, "1h": 90, "15m": 30}
        delta = offsets.get(timeframe, 365)
        return (now - timedelta(days=delta)).strftime("%Y%m%d")

    @property
    def progress(self) -> dict:
        return {**self._progress, "scanning": self._scanning}

    @property
    def last_results(self) -> dict | None:
        return self._last_results
