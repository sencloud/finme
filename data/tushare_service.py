"""Tushare data service for futures market data.

Ported from main/services/tushare-service.js.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


class TushareService:
    API_URL = "http://api.tushare.pro"

    def __init__(self, token: str = "", local_cache=None) -> None:
        self.token = token
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._cache_expiry = 60.0
        self._local_cache = local_cache

    def set_local_cache(self, cache) -> None:
        self._local_cache = cache

    def set_token(self, token: str) -> None:
        self.token = token
        self._cache.clear()

    def request(self, api_name: str, params: dict | None = None,
                retries: int = 2) -> list[dict]:
        if not self.token or not self.token.strip():
            raise RuntimeError("Tushare token未配置")

        params = params or {}
        cache_key = f"{api_name}_{params}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < self._cache_expiry:
            return cached[1]

        timeout = 30.0
        start_date = params.get("start_date", "")
        end_date = params.get("end_date", "")
        if start_date and end_date:
            try:
                sy = int(start_date[:4])
                ey = int(end_date[:4])
                if ey - sy > 5:
                    timeout = 60.0
            except (ValueError, IndexError):
                pass

        fields = params.pop("fields", "") if "fields" in params else ""
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    logger.info("[TushareService] 第%d次重试: %s", attempt, api_name)
                    time.sleep(1.0 * attempt)

                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(
                        self.API_URL,
                        json={
                            "api_name": api_name,
                            "token": self.token,
                            "params": params,
                            "fields": fields,
                        },
                    )
                    data = resp.json()

                if data.get("code") != 0:
                    msg = data.get("msg", "Tushare API错误")
                    raise RuntimeError(f"{msg} [api={api_name}, params={params}]")

                result = self._parse_response(data)
                self._cache[cache_key] = (time.time(), result)
                return result

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                logger.warning("[TushareService] 请求超时 (尝试%d/%d)", attempt + 1, retries + 1)
            except Exception as e:
                last_error = e
                logger.error("[TushareService] API请求失败: %s", e)
                break

        raise last_error or RuntimeError("TushareService request failed")

    @staticmethod
    def _parse_response(response: dict) -> list[dict]:
        data = response.get("data", {})
        fields = data.get("fields", [])
        items = data.get("items", [])
        if not fields or not items:
            return []
        return [dict(zip(fields, item)) for item in items]

    # ------------------------------------------------------------------
    # Futures data endpoints
    # ------------------------------------------------------------------

    def get_futures_daily(self, ts_code: str, start_date: str,
                          end_date: str) -> list[dict]:
        if self._local_cache:
            return self._cached_daily(ts_code, start_date, end_date)
        return self.request("fut_daily", {
            "ts_code": ts_code,
            "start_date": start_date,
            "end_date": end_date,
        })

    def _cached_daily(self, ts_code: str, start_date: str,
                      end_date: str) -> list[dict]:
        lc = self._local_cache
        latest = lc.get_latest_date(ts_code, "daily")
        if latest and self._to_yyyymmdd(latest) >= end_date:
            logger.info("[TushareService] 日线缓存命中 %s (%s~%s)", ts_code, start_date, end_date)
            return lc.get_bars(ts_code, "daily", start_date, end_date)

        fetch_start = self._to_yyyymmdd(latest) if latest else start_date
        logger.info("[TushareService] 日线增量拉取 %s (%s~%s)", ts_code, fetch_start, end_date)
        new_bars = self.request("fut_daily", {
            "ts_code": ts_code,
            "start_date": fetch_start,
            "end_date": end_date,
        })
        lc.merge_and_save(ts_code, "daily", new_bars)
        return lc.get_bars(ts_code, "daily", start_date, end_date)

    def get_futures_minutes(self, ts_code: str, freq: str = "15min",
                            start_date: str = "", end_date: str = "") -> list[dict]:
        if self._local_cache and start_date and end_date:
            return self._cached_minutes(ts_code, freq, start_date, end_date)
        params: dict = {"ts_code": ts_code, "freq": freq}
        if start_date:
            params["start_date"] = self._to_minutes_datetime(start_date, end_of_day=False)
        if end_date:
            params["end_date"] = self._to_minutes_datetime(end_date, end_of_day=True)
        return self.request("ft_mins", params)

    def _cached_minutes(self, ts_code: str, freq: str,
                        start_date: str, end_date: str) -> list[dict]:
        lc = self._local_cache
        cache_freq = freq.replace("min", "min")
        latest = lc.get_latest_date(ts_code, cache_freq)
        if latest and self._to_yyyymmdd(latest) >= self._to_yyyymmdd(end_date):
            logger.info("[TushareService] 分钟缓存命中 %s/%s (%s~%s)", ts_code, freq, start_date, end_date)
            return lc.get_bars(ts_code, cache_freq, start_date, end_date)

        fetch_start = self._to_yyyymmdd(latest) if latest else start_date
        logger.info("[TushareService] 分钟增量拉取 %s/%s (%s~%s)", ts_code, freq, fetch_start, end_date)
        new_bars = self.request("ft_mins", {
            "ts_code": ts_code, "freq": freq,
            "start_date": self._to_minutes_datetime(fetch_start, end_of_day=False),
            "end_date": self._to_minutes_datetime(end_date, end_of_day=True),
        })
        lc.merge_and_save(ts_code, cache_freq, new_bars)
        return lc.get_bars(ts_code, cache_freq, start_date, end_date)

    @staticmethod
    def _to_yyyymmdd(date_str: str) -> str:
        """Normalize any date string to YYYYMMDD for API params.

        Handles both ``YYYYMMDD`` and ``YYYY-MM-DD ...`` formats.
        """
        s = date_str.strip()[:10]
        return s.replace("-", "")

    @staticmethod
    def _to_minutes_datetime(date_str: str, end_of_day: bool = False) -> str:
        """Convert YYYYMMDD or YYYY-MM-DD to ``YYYY-MM-DD HH:MM:SS`` for ft_mins API."""
        s = date_str.strip().replace("-", "")[:8]
        if len(s) < 8:
            return date_str
        formatted = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return f"{formatted} 23:59:59" if end_of_day else f"{formatted} 00:00:00"

    def get_futures_weekly(self, ts_code: str, start_date: str,
                           end_date: str) -> list[dict]:
        if self._local_cache:
            return self._cached_weekly(ts_code, start_date, end_date)
        return self.request("fut_weekly_monthly", {
            "ts_code": ts_code,
            "freq": "week",
            "start_date": start_date,
            "end_date": end_date,
        })

    def _cached_weekly(self, ts_code: str, start_date: str,
                       end_date: str) -> list[dict]:
        lc = self._local_cache
        latest = lc.get_latest_date(ts_code, "weekly")
        if latest and self._to_yyyymmdd(latest) >= end_date:
            logger.info("[TushareService] 周线缓存命中 %s (%s~%s)", ts_code, start_date, end_date)
            return lc.get_bars(ts_code, "weekly", start_date, end_date)

        fetch_start = self._to_yyyymmdd(latest) if latest else start_date
        logger.info("[TushareService] 周线增量拉取 %s (%s~%s)", ts_code, fetch_start, end_date)
        new_bars = self.request("fut_weekly_monthly", {
            "ts_code": ts_code,
            "freq": "week",
            "start_date": fetch_start,
            "end_date": end_date,
        })
        lc.merge_and_save(ts_code, "weekly", new_bars)
        return lc.get_bars(ts_code, "weekly", start_date, end_date)

    def get_futures_basic(self, exchange: str, fut_type: str = "") -> list[dict]:
        params: dict = {"exchange": exchange}
        if fut_type:
            params["fut_type"] = fut_type
        return self.request("fut_basic", params)

    def get_futures_mapping(self, ts_code: str, start_date: str = "",
                            end_date: str = "",
                            trade_date: str = "") -> list[dict]:
        params: dict = {"ts_code": ts_code}
        if trade_date:
            params["trade_date"] = trade_date
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self.request("fut_mapping", params)

    def get_dominant_minutes(self, ts_code: str, freq: str,
                              start_date: str, end_date: str,
                              progress_fn=None) -> list[dict]:
        """Fetch minute data across dominant-contract rollovers.

        ``ft_mins`` requires a specific contract (e.g. C2501.DCE).
        This method uses ``fut_mapping`` to discover which contract was
        dominant on each trading day, groups consecutive days by contract,
        then fetches and concatenates minute bars for each segment.

        Results are cached under ``{ts_code}_dominant/{freq}`` so that
        subsequent calls only fetch incremental new bars.
        """
        composite_key = f"{ts_code}_dominant"
        if self._local_cache:
            latest = self._local_cache.get_latest_date(composite_key, freq)
            if latest and self._to_yyyymmdd(latest) >= end_date:
                logger.info("[TushareService] 主力分钟合成缓存命中 %s/%s", ts_code, freq)
                return self._local_cache.get_bars(composite_key, freq, start_date, end_date)
            fetch_start = self._to_yyyymmdd(latest) if latest else start_date
        else:
            fetch_start = start_date

        mapping = self.get_futures_mapping(ts_code, start_date=fetch_start, end_date=end_date)
        if not mapping:
            logger.warning("[TushareService] 无法获取 %s 的主力合约映射", ts_code)
            return self._local_cache.get_bars(composite_key, freq, start_date, end_date) if self._local_cache else []

        segments: list[dict] = []
        for row in sorted(mapping, key=lambda r: r.get("trade_date", "")):
            code = row.get("mapping_ts_code", "")
            td = row.get("trade_date", "")
            if not code or not td:
                continue
            if segments and segments[-1]["code"] == code:
                segments[-1]["end"] = td
            else:
                segments.append({"code": code, "start": td, "end": td})

        all_bars: list[dict] = []
        total = len(segments)
        for idx, seg in enumerate(segments):
            if progress_fn:
                progress_fn(seg["code"], idx + 1, total)
            try:
                bars = self.get_futures_minutes(seg["code"], freq, seg["start"], seg["end"])
                if bars:
                    for b in bars:
                        b["contract"] = seg["code"]
                    all_bars.extend(bars)
            except Exception as e:
                logger.warning("[TushareService] %s %s 分钟数据失败: %s", seg["code"], freq, e)
            if idx < total - 1:
                time.sleep(0.3)

        if self._local_cache and all_bars:
            self._local_cache.merge_and_save(composite_key, freq, all_bars)
            return self._local_cache.get_bars(composite_key, freq, start_date, end_date)

        return all_bars

    def get_futures_holding(self, symbol: str, start_date: str = "",
                            end_date: str = "") -> list[dict]:
        params: dict = {"symbol": symbol}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self.request("fut_holding", params)

    def get_futures_contracts_by_activity(self, symbol: str, exchange: str) -> dict:
        try:
            all_contracts = self.request("fut_basic", {"exchange": exchange, "fut_code": symbol})
            if not all_contracts:
                return {"main": [], "near": [], "far": []}

            now = date.today()
            active = []
            for c in all_contracts:
                dl = c.get("delist_date", "")
                if dl:
                    try:
                        delist = datetime.strptime(dl, "%Y%m%d").date()
                        if delist <= now:
                            continue
                    except Exception:
                        pass
                active.append(c)

            end_d = self.format_date(now)
            start_d = self.get_date_before(5)
            with_volume = []
            for c in active:
                try:
                    daily = self.request("fut_daily", {"ts_code": c["ts_code"], "start_date": start_d, "end_date": end_d})
                    if daily:
                        latest = daily[-1]
                        with_volume.append({**c, "volume": latest.get("vol", 0), "oi": latest.get("oi", 0)})
                except Exception:
                    pass

            with_volume.sort(key=lambda x: x.get("oi", 0), reverse=True)
            by_date = sorted(with_volume, key=lambda x: x.get("delist_date", "99999999"))

            main = with_volume[:3]
            main_codes = {c["ts_code"] for c in main}
            near = [c for c in by_date if c["ts_code"] not in main_codes][:3]
            near_codes = {c["ts_code"] for c in near}
            far = [c for c in by_date if c["ts_code"] not in main_codes and c["ts_code"] not in near_codes][-3:]
            far.reverse()

            def _brief(lst):
                return [{"code": c.get("ts_code", ""), "name": c.get("name", "")} for c in lst]

            return {"main": _brief(main), "near": _brief(near), "far": _brief(far)}
        except Exception as e:
            logger.error("[TushareService] 获取%s期货合约失败: %s", symbol, e)
            return {"main": [], "near": [], "far": []}

    def search_instruments(self, keyword: str) -> dict:
        try:
            exchanges = ["DCE", "CZCE", "SHFE", "INE"]
            all_data: list[dict] = []
            for ex in exchanges:
                try:
                    data = self.get_futures_basic(ex)
                    if data:
                        all_data.extend(data)
                except Exception:
                    pass

            if keyword and keyword.strip():
                kw = keyword.lower().strip()
                all_data = [
                    item for item in all_data
                    if kw in (item.get("ts_code") or item.get("symbol") or "").lower()
                    or kw in (item.get("name") or "").lower()
                ]

            return {"success": True, "data": all_data[:100]}
        except Exception as e:
            return {"success": False, "data": [], "message": str(e)}

    def get_last_trade_date(self) -> str:
        result = self.request("trade_cal", {
            "exchange": "DCE",
            "is_open": 1,
            "start_date": self.get_date_before(10),
            "end_date": self.format_date(date.today()),
        })
        if result:
            result.sort(key=lambda r: r.get("cal_date", ""), reverse=True)
            return result[0].get("cal_date", self.format_date(date.today()))
        return self.format_date(date.today())

    def get_warehouse_receipt(self, symbol: str = "", trade_date: str = "",
                              start_date: str = "", end_date: str = "",
                              exchange: str = "") -> list[dict]:
        params: dict = {}
        if symbol:
            params["symbol"] = symbol
        if trade_date:
            params["trade_date"] = trade_date
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if exchange:
            params["exchange"] = exchange
        params["fields"] = ("trade_date,symbol,pname,warehouse,wh_id,pre_vol,vol,vol_chg,"
                            "area,year,grade,brand,origin,premium,is_settle,unit,exchange")
        return self.request("fut_wsr", params)

    def get_corn_warehouse_receipts(self, days: int = 60) -> list[dict]:
        end_d = self.format_date(date.today())
        start_d = self.get_date_before(days)
        data = self.get_warehouse_receipt("C", start_date=start_d, end_date=end_d, exchange="DCE")
        if not data:
            return []

        daily_map: dict[str, dict] = {}
        for row in data:
            td = row.get("trade_date", "")
            if td not in daily_map:
                daily_map[td] = {"trade_date": td, "total_vol": 0, "total_chg": 0, "warehouses": []}
            day = daily_map[td]
            day["total_vol"] += row.get("vol", 0) or 0
            day["total_chg"] += row.get("vol_chg", 0) or 0
            day["warehouses"].append({
                "warehouse": row.get("warehouse") or row.get("pname", ""),
                "vol": row.get("vol", 0) or 0,
                "vol_chg": row.get("vol_chg", 0) or 0,
                "area": row.get("area", ""),
            })

        return sorted(daily_map.values(), key=lambda d: d["trade_date"])

    def resolve_execution_contract(self, trend_series_code: str) -> dict:
        try:
            mapping = self.get_futures_mapping(trend_series_code)
            if not mapping:
                return {"trendSeriesCode": trend_series_code, "executionTsCode": None, "mappingDate": None}
            sorted_mapping = sorted(mapping, key=lambda r: r.get("trade_date", ""), reverse=True)
            latest = sorted_mapping[0]
            code = latest.get("mapping_ts_code")
            td = latest.get("trade_date")
            logger.info("[TushareService] %s 主力合约: %s (映射日期=%s)", trend_series_code, code, td)
            return {
                "trendSeriesCode": trend_series_code,
                "executionTsCode": code,
                "mappingDate": td,
            }
        except Exception as e:
            logger.error("[TushareService] resolveExecutionContract 失败: %s", e)
            return {"trendSeriesCode": trend_series_code, "executionTsCode": None, "mappingDate": None}

    def get_all_futures_varieties(self) -> list[dict]:
        exchanges = ["DCE", "CZCE", "SHFE", "INE", "CFFEX"]
        all_contracts: list[dict] = []
        for ex in exchanges:
            try:
                data = self.get_futures_basic(ex)
                if data:
                    all_contracts.extend(data)
            except Exception as e:
                logger.warning("[TushareService] 获取 %s 品种失败: %s", ex, e)

        import re
        prefix_map: dict[str, dict] = {}
        for c in all_contracts:
            code = (c.get("ts_code") or c.get("symbol") or "").split(".")[0]
            match = re.match(r"^([A-Z]+)", code, re.IGNORECASE)
            if not match:
                continue
            prefix = match.group(1).upper()
            if prefix in prefix_map:
                continue
            name = c.get("name", prefix)
            name = re.sub(r"\d+", "", name).strip()
            prefix_map[prefix] = {
                "prefix": prefix,
                "name": name,
                "exchange": c.get("exchange", ""),
                "multiplier": c.get("multiplier"),
            }
        return list(prefix_map.values())

    # ------------------------------------------------------------------
    # Stock data endpoints
    # ------------------------------------------------------------------

    def get_stock_daily(self, ts_code: str, start_date: str,
                        end_date: str) -> list[dict]:
        if self._local_cache:
            return self._cached_stock_daily(ts_code, start_date, end_date)
        return self.request("daily", {
            "ts_code": ts_code,
            "start_date": start_date,
            "end_date": end_date,
        })

    def _cached_stock_daily(self, ts_code: str, start_date: str,
                            end_date: str) -> list[dict]:
        lc = self._local_cache
        cache_key = f"stock_{ts_code}"
        latest = lc.get_latest_date(cache_key, "daily")
        if latest and self._to_yyyymmdd(latest) >= end_date:
            logger.info("[TushareService] 股票日线缓存命中 %s (%s~%s)", ts_code, start_date, end_date)
            return lc.get_bars(cache_key, "daily", start_date, end_date)

        fetch_start = self._to_yyyymmdd(latest) if latest else start_date
        logger.info("[TushareService] 股票日线增量拉取 %s (%s~%s)", ts_code, fetch_start, end_date)
        new_bars = self.request("daily", {
            "ts_code": ts_code,
            "start_date": fetch_start,
            "end_date": end_date,
        })
        lc.merge_and_save(cache_key, "daily", new_bars)
        return lc.get_bars(cache_key, "daily", start_date, end_date)

    def get_stock_weekly(self, ts_code: str, start_date: str,
                         end_date: str) -> list[dict]:
        if self._local_cache:
            return self._cached_stock_weekly(ts_code, start_date, end_date)
        return self.request("weekly", {
            "ts_code": ts_code,
            "start_date": start_date,
            "end_date": end_date,
        })

    def _cached_stock_weekly(self, ts_code: str, start_date: str,
                             end_date: str) -> list[dict]:
        lc = self._local_cache
        cache_key = f"stock_{ts_code}"
        latest = lc.get_latest_date(cache_key, "weekly")
        if latest and self._to_yyyymmdd(latest) >= end_date:
            logger.info("[TushareService] 股票周线缓存命中 %s (%s~%s)", ts_code, start_date, end_date)
            return lc.get_bars(cache_key, "weekly", start_date, end_date)

        fetch_start = self._to_yyyymmdd(latest) if latest else start_date
        logger.info("[TushareService] 股票周线增量拉取 %s (%s~%s)", ts_code, fetch_start, end_date)
        new_bars = self.request("weekly", {
            "ts_code": ts_code,
            "start_date": fetch_start,
            "end_date": end_date,
        })
        lc.merge_and_save(cache_key, "weekly", new_bars)
        return lc.get_bars(cache_key, "weekly", start_date, end_date)

    def get_stock_minutes(self, ts_code: str, freq: str = "15min",
                          start_date: str = "", end_date: str = "") -> list[dict]:
        if self._local_cache and start_date and end_date:
            return self._cached_stock_minutes(ts_code, freq, start_date, end_date)
        return self._fetch_stock_minutes_segmented(ts_code, freq, start_date, end_date)

    def _fetch_stock_minutes_segmented(self, ts_code: str, freq: str,
                                       start_date: str, end_date: str) -> list[dict]:
        """Fetch stock minutes in 6-month segments to avoid the 8000-row API limit."""
        if not start_date or not end_date:
            params: dict = {"ts_code": ts_code, "freq": freq}
            if start_date:
                params["start_date"] = self._to_minutes_datetime(start_date, end_of_day=False)
            if end_date:
                params["end_date"] = self._to_minutes_datetime(end_date, end_of_day=True)
            return self.request("stk_mins", params)

        sd = datetime.strptime(self._to_yyyymmdd(start_date), "%Y%m%d")
        ed = datetime.strptime(self._to_yyyymmdd(end_date), "%Y%m%d")
        all_bars: list[dict] = []
        seg_start = sd

        while seg_start < ed:
            seg_end = min(seg_start + timedelta(days=180), ed)
            bars = self.request("stk_mins", {
                "ts_code": ts_code, "freq": freq,
                "start_date": self._to_minutes_datetime(seg_start.strftime("%Y%m%d"), end_of_day=False),
                "end_date": self._to_minutes_datetime(seg_end.strftime("%Y%m%d"), end_of_day=True),
            })
            if bars:
                all_bars.extend(bars)
            seg_start = seg_end + timedelta(days=1)
            if seg_start < ed:
                time.sleep(0.3)

        return all_bars

    def _cached_stock_minutes(self, ts_code: str, freq: str,
                              start_date: str, end_date: str) -> list[dict]:
        lc = self._local_cache
        cache_key = f"stock_{ts_code}"
        cache_freq = freq.replace("min", "min")
        latest = lc.get_latest_date(cache_key, cache_freq)
        if latest and self._to_yyyymmdd(latest) >= self._to_yyyymmdd(end_date):
            logger.info("[TushareService] 股票分钟缓存命中 %s/%s (%s~%s)", ts_code, freq, start_date, end_date)
            return lc.get_bars(cache_key, cache_freq, start_date, end_date)

        fetch_start = self._to_yyyymmdd(latest) if latest else start_date
        logger.info("[TushareService] 股票分钟增量拉取 %s/%s (%s~%s)", ts_code, freq, fetch_start, end_date)
        new_bars = self._fetch_stock_minutes_segmented(ts_code, freq, fetch_start, end_date)
        lc.merge_and_save(cache_key, cache_freq, new_bars)
        return lc.get_bars(cache_key, cache_freq, start_date, end_date)

    # ------------------------------------------------------------------
    # Stock list endpoints
    # ------------------------------------------------------------------

    _INDEX_CODE_MAP = {
        "HS300": "399300.SZ",
        "ZZ500": "000905.SH",
        "ZZ1000": "000852.SH",
        "SZ50": "000016.SH",
        "CYB": "399006.SZ",
        "KC50": "000688.SH",
    }

    def get_index_members(self, index_name: str) -> list[str]:
        """Return ts_code list for a named index (e.g. HS300, ZZ500)."""
        index_code = self._INDEX_CODE_MAP.get(index_name.upper())
        if not index_code:
            raise ValueError(f"未知指数: {index_name}，支持: {', '.join(self._INDEX_CODE_MAP)}")

        rows = self.request("index_weight", {
            "index_code": index_code,
            "start_date": self.format_date(date.today() - timedelta(days=60)),
            "end_date": self.format_date(date.today()),
        })
        if not rows:
            raise RuntimeError(f"无法获取 {index_name} 成分股数据")

        latest_date = max(r.get("trade_date", "") for r in rows)
        codes = sorted({r["con_code"] for r in rows if r.get("trade_date") == latest_date})
        return codes

    def get_all_stocks(self) -> list[dict]:
        """Return all currently listed A-share stocks."""
        return self.request("stock_basic", {
            "exchange": "",
            "list_status": "L",
            "fields": "ts_code,symbol,name,area,industry,list_date",
        })

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def format_date(d: date | datetime) -> str:
        return d.strftime("%Y%m%d")

    @staticmethod
    def get_date_before(days: int) -> str:
        return TushareService.format_date(date.today() - timedelta(days=days))
