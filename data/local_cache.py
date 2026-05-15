"""Persistent local file cache for market data.

Stores K-line bars as JSON files on disk, enabling incremental fetching
so that only new bars beyond the latest cached date are pulled from
the remote API.

Cache layout::

    data_cache/
      C_DCE/
        daily.json
        60min.json
        15min.json
      M_DCE/
        daily.json
        ...
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DATE_KEY_MAP = {
    "daily": "trade_date",
    "weekly": "trade_date",
    "60min": "trade_time",
    "15min": "trade_time",
    "5min": "trade_time",
}


def _comparable_date(s: str, pad: str = "0") -> str:
    """Strip to digits and pad to 14 chars for consistent date comparison.

    Handles both ``YYYYMMDD`` and ``YYYY-MM-DD HH:MM:SS`` formats.
    Examples:
        ``"20230110"``              -> ``"20230110000000"``
        ``"2023-01-10 09:15:00"``   -> ``"20230110091500"``
    """
    digits = "".join(c for c in s if c.isdigit())
    return digits.ljust(14, pad)


class LocalDataCache:
    """Append-only bar cache with incremental update support."""

    def __init__(self, cache_dir: str | Path = "data_cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def _cache_path(self, ts_code: str, freq: str) -> Path:
        safe_code = ts_code.replace(".", "_")
        d = self.cache_dir / safe_code
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{freq}.json"

    def load(self, ts_code: str, freq: str) -> list[dict]:
        path = self._cache_path(ts_code, freq)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return payload.get("bars", []) if isinstance(payload, dict) else payload
        except Exception as e:
            logger.warning("[LocalCache] 读取缓存失败 %s: %s", path, e)
            return []

    def save(self, ts_code: str, freq: str, bars: list[dict]) -> None:
        if not bars:
            return
        path = self._cache_path(ts_code, freq)
        payload = {
            "ts_code": ts_code,
            "freq": freq,
            "updated_at": datetime.now().isoformat(),
            "count": len(bars),
            "bars": bars,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as e:
            logger.error("[LocalCache] 写缓存失败 %s: %s", path, e)

    def get_latest_date(self, ts_code: str, freq: str) -> str | None:
        """Return the latest date string in the cache, or None."""
        bars = self.load(ts_code, freq)
        if not bars:
            return None
        date_key = _DATE_KEY_MAP.get(freq, "trade_date")
        dates = [b.get(date_key, "") for b in bars if b.get(date_key)]
        return max(dates) if dates else None

    def merge_and_save(self, ts_code: str, freq: str,
                       new_bars: list[dict]) -> list[dict]:
        """Merge *new_bars* into the existing cache, deduplicate, sort, save,
        and return the complete bar list."""
        existing = self.load(ts_code, freq)
        date_key = _DATE_KEY_MAP.get(freq, "trade_date")

        seen: dict[str, dict] = {}
        for b in existing:
            dk = b.get(date_key, "")
            if dk:
                seen[dk] = b
        for b in new_bars:
            dk = b.get(date_key, "")
            if dk:
                seen[dk] = b

        merged = sorted(seen.values(), key=lambda x: x.get(date_key, ""))
        self.save(ts_code, freq, merged)
        return merged

    def get_bars(self, ts_code: str, freq: str,
                 start_date: str = "", end_date: str = "") -> list[dict]:
        """Return cached bars filtered to [start_date, end_date].

        Handles mixed date formats (``YYYYMMDD`` vs ``YYYY-MM-DD HH:MM:SS``)
        by normalising both filter and bar dates to digit-only strings.
        """
        bars = self.load(ts_code, freq)
        if not bars:
            return []
        date_key = _DATE_KEY_MAP.get(freq, "trade_date")
        result = bars
        if start_date:
            s = _comparable_date(start_date, pad="0")
            result = [b for b in result if _comparable_date(b.get(date_key, ""), pad="0") >= s]
        if end_date:
            e = _comparable_date(end_date, pad="9")
            result = [b for b in result if _comparable_date(b.get(date_key, ""), pad="0") <= e]
        return result

    def clear(self, ts_code: str | None = None, freq: str | None = None) -> None:
        if ts_code and freq:
            p = self._cache_path(ts_code, freq)
            if p.exists():
                p.unlink()
        elif ts_code:
            safe = ts_code.replace(".", "_")
            d = self.cache_dir / safe
            if d.is_dir():
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()
        else:
            import shutil
            if self.cache_dir.is_dir():
                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
