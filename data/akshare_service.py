"""AkShare real-time minute data service for live trading.

Uses Sina futures data via akshare to get real-time intraday bars
that Tushare cannot provide.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_FREQ_MAP = {"15min": "15", "60min": "60", "5min": "5", "30min": "30", "1min": "1"}

_TUSHARE_TO_SINA_EXCHANGE = {"SHF": "SHFE", "ZCE": "CZCE", "CFE": "CFFEX"}


def _ts_code_to_sina_symbol(ts_code: str) -> str:
    """Convert Tushare ts_code like 'RB2605.SHF' to Sina symbol 'RB2605'."""
    return ts_code.split(".")[0].upper()


class AkShareService:
    """Provides real-time minute bar data from Sina via akshare."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._cache_ttl = 45.0
        self._ak = None

    def _get_ak(self):
        if self._ak is None:
            try:
                import akshare as ak
                self._ak = ak
            except ImportError:
                raise RuntimeError("akshare 未安装，请执行: pip install akshare")
        return self._ak

    def get_realtime_minutes(self, ts_code: str, freq: str = "15min") -> list[dict]:
        """Fetch real-time minute bars for a futures contract.

        Args:
            ts_code: Tushare-style code like 'RB2605.SHF' or plain 'RB2605'
            freq: '15min', '60min', '5min', '30min', '1min'

        Returns:
            List of bar dicts with keys: trade_time, open, high, low, close, vol
            (same schema as Tushare ft_mins for seamless integration)
        """
        symbol = _ts_code_to_sina_symbol(ts_code)
        period = _FREQ_MAP.get(freq, "15")

        cache_key = f"{symbol}_{period}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < self._cache_ttl:
            return cached[1]

        ak = self._get_ak()
        try:
            df = ak.futures_zh_minute_sina(symbol=symbol, period=period)
        except Exception as e:
            logger.error("[AkShare] %s %smin 数据获取失败: %s", symbol, period, e)
            if cached:
                return cached[1]
            return []

        if df is None or df.empty:
            logger.warning("[AkShare] %s %smin 返回空数据", symbol, period)
            return []

        bars = self._transform(df)
        self._cache[cache_key] = (time.time(), bars)
        logger.info("[AkShare] %s %smin 获取 %d 根K线 (最新: %s)",
                    symbol, period, len(bars),
                    bars[-1].get("trade_time", "") if bars else "--")
        return bars

    @staticmethod
    def _transform(df) -> list[dict]:
        """Convert AkShare DataFrame to Tushare-compatible dict list."""
        result = []
        for _, row in df.iterrows():
            dt_val = row.get("datetime") or row.get("date")
            if dt_val is None:
                continue
            try:
                if hasattr(dt_val, "strftime"):
                    dt_str = dt_val.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    dt_str = str(dt_val)
            except Exception:
                dt_str = str(dt_val)

            try:
                result.append({
                    "trade_time": dt_str,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "vol": float(row.get("volume", 0) or 0),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return result

    def clear_cache(self) -> None:
        self._cache.clear()
