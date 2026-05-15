"""Chanlun combo strategy helpers.

Ported from shared/strategy/chanlun-combo.js.
"""

from __future__ import annotations

import math

SIGNAL_LABELS = {
    "buy1": "一买", "buy2": "二买", "buy3": "三买",
    "sell1": "一卖", "sell2": "二卖", "sell3": "三卖",
    "semiBuy2": "类二买", "semiBuy3": "类三买",
    "semiSell2": "类二卖", "semiSell3": "类三卖",
}

COMBO_PRESETS = {
    "balanced": {
        "label": "综合平衡",
        "description": "默认方案，兼顾二买二卖、影线和突破。",
        "minScore": 64,
        "allowCounterTrend": False,
    },
    "shadowReversal": {
        "label": "影线反转",
        "description": "适合区间边沿反抽，强调长下影/长上影与中枢边沿共振。",
        "minScore": 54,
        "allowCounterTrend": True,
    },
    "secondEntry": {
        "label": "二买二卖接力",
        "description": "顺大方向回撤后再启动，强调二买/二卖和类二买/类二卖。",
        "minScore": 58,
        "allowCounterTrend": False,
    },
    "hubRevert": {
        "label": "中枢回补",
        "description": "适合整理行情，在1小时中枢边沿做回补和假突破回归。",
        "minScore": 56,
        "allowCounterTrend": True,
    },
    "breakoutTrend": {
        "label": "三买三卖突破",
        "description": "适合整理后的方向选择，重点做三买/三卖和类三买/类三卖。",
        "minScore": 60,
        "allowCounterTrend": False,
    },
}


def is_buy_type(signal_type: str) -> bool:
    return "buy" in (signal_type or "").lower()


def is_sell_type(signal_type: str) -> bool:
    return "sell" in (signal_type or "").lower()


def get_direction_from_type(signal_type: str) -> str | None:
    if is_buy_type(signal_type):
        return "long"
    if is_sell_type(signal_type):
        return "short"
    return None


def get_signal_label(signal_type: str) -> str:
    return SIGNAL_LABELS.get(signal_type, signal_type or "")


def get_signal_family(signal_type: str) -> str:
    mapping = {
        "buy1": "一买", "buy2": "二买", "buy3": "三买",
        "sell1": "一卖", "sell2": "二卖", "sell3": "三卖",
        "semiBuy2": "类二买", "semiBuy3": "类三买",
        "semiSell2": "类二卖", "semiSell3": "类三卖",
    }
    return mapping.get(signal_type or "", signal_type or "未知")


def _parse_datetime(date_input) -> "dt":
    """Parse various date input formats into a datetime object."""
    from datetime import datetime as dt
    if date_input is None:
        return dt.now()
    if isinstance(date_input, str):
        try:
            return dt.fromisoformat(date_input.replace("/", "-"))
        except Exception:
            return dt.now()
    if isinstance(date_input, (int, float)):
        return dt.fromtimestamp(date_input / 1000)
    return date_input


def get_time_context(date_input=None) -> dict:
    """Determine trading session segment and quality score."""
    now = _parse_datetime(date_input)
    hh = now.hour
    mm = now.minute
    hhmm = hh * 100 + mm

    segment = "其他"
    quality = "low"
    score = -2

    if 900 <= hhmm < 1015:
        segment, quality, score = "早盘前段", "high", 8
    elif 1030 <= hhmm < 1130:
        segment, quality, score = "早盘后段", "high", 8
    elif 1330 <= hhmm < 1430:
        segment, quality, score = "午盘前段", "medium", 5
    elif 1430 <= hhmm < 1445:
        segment, quality, score = "午盘尾盘", "low", -3

    return {"hhmm": hhmm, "segment": segment, "quality": quality, "score": score, "blockNew": False}


# Session boundary times for 15-min bars (bar end timestamps that sit at
# session open/close boundaries). Bars ending at these times produce signals
# that are difficult or impossible to execute in live trading.

SESSION_CLOSE_BARS = {
    1015: {"label": "早盘休盘", "gap_minutes": 15, "next_session": "10:30"},
    1130: {"label": "午间休市", "gap_minutes": 120, "next_session": "13:30"},
    1500: {"label": "日盘收盘", "gap_minutes": 360, "next_session": "21:00"},
    1515: {"label": "日盘收盘(金融期货)", "gap_minutes": 345, "next_session": "21:00"},
    2300: {"label": "夜盘收盘(基础)", "gap_minutes": 600, "next_session": "09:00"},
    2330: {"label": "夜盘收盘(化工等)", "gap_minutes": 570, "next_session": "09:00"},
    100:  {"label": "夜盘收盘(有色)", "gap_minutes": 480, "next_session": "09:00"},
    230:  {"label": "夜盘收盘(贵金属)", "gap_minutes": 390, "next_session": "09:00"},
}

SESSION_OPEN_BARS = {
    915:  {"label": "日盘开盘首根", "note": "开盘跳空风险"},
    2115: {"label": "夜盘开盘首根", "note": "夜盘跳空风险"},
    1045: {"label": "早盘复盘首根", "note": "休盘后首根"},
    1345: {"label": "午盘开盘首根", "note": "午间跳空风险"},
}

NIGHT_SESSION_PRODUCTS = {
    "basic":   {"close": 2300, "products": ["RB", "HC", "I", "J", "JM", "SF", "SM", "SS",
                                              "FG", "SA", "TA", "MA", "UR", "SR", "CF", "RM",
                                              "OI", "C", "CS", "A", "M", "Y", "P", "JD",
                                              "PP", "V", "L", "EG", "EB", "PG"]},
    "extended": {"close": 2330, "products": ["BU", "RU"]},
    "late":     {"close": 100,  "products": ["CU", "AL", "ZN", "NI", "SC"]},
    "precious": {"close": 230,  "products": ["AU", "AG"]},
    "none":     {"close": None, "products": []},
}


def get_night_close_time(variety_prefix: str) -> int | None:
    """Return night session close hhmm for a variety, or None if no night session."""
    prefix = variety_prefix.upper()
    for _group, info in NIGHT_SESSION_PRODUCTS.items():
        if prefix in info["products"]:
            return info["close"]
    return None


def check_signal_tradability(date_input, variety_prefix: str = "") -> dict:
    """Check whether a signal at the given time can be executed in live trading.

    Returns a dict with:
      - tradeable: bool
      - reason: str (empty if tradeable)
      - boundary_type: "session_close" | "session_open" | None
      - gap_minutes: estimated gap to next session (0 if tradeable)
      - next_session: next available trading time
      - suggestion: recommended action for untradeable signals
    """
    now = _parse_datetime(date_input)
    hhmm = now.hour * 100 + now.minute

    for close_hhmm, info in SESSION_CLOSE_BARS.items():
        if hhmm == close_hhmm:
            if variety_prefix and close_hhmm in (2300, 2330, 100, 230):
                actual_close = get_night_close_time(variety_prefix)
                if actual_close is not None and actual_close != close_hhmm:
                    continue

            gap = info["gap_minutes"]
            severity = "high" if gap >= 300 else ("medium" if gap >= 60 else "low")
            return {
                "tradeable": False,
                "reason": f'{info["label"]}信号，距下一交易时段{gap}分钟',
                "boundary_type": "session_close",
                "gap_minutes": gap,
                "next_session": info["next_session"],
                "severity": severity,
                "suggestion": _build_close_suggestion(info, severity),
            }

    for open_hhmm, info in SESSION_OPEN_BARS.items():
        if hhmm == open_hhmm:
            return {
                "tradeable": True,
                "reason": f'{info["label"]}，{info["note"]}',
                "boundary_type": "session_open",
                "gap_minutes": 0,
                "next_session": None,
                "severity": "low",
                "suggestion": "可交易，但注意开盘价可能跳空偏离信号价，建议用限价单",
            }

    return {
        "tradeable": True,
        "reason": "",
        "boundary_type": None,
        "gap_minutes": 0,
        "next_session": None,
        "severity": "none",
        "suggestion": "",
    }


def _build_close_suggestion(info: dict, severity: str) -> str:
    next_s = info["next_session"]
    if severity == "high":
        return (f"不建议执行！隔夜/跨时段风险极高。"
                f"如仍需交易，在{next_s}开盘后观察价格是否仍在止损范围内，"
                f"用限价单挂入场价，若跳空超过1倍ATR则放弃")
    if severity == "medium":
        return (f"谨慎执行。可在{next_s}开盘时用限价单挂入场价，"
                f"注意午间可能有消息面变化导致跳空")
    return f"短暂休盘，可在{next_s}复盘后立即用限价单执行"


def get_corn_season_phase(date_input=None) -> dict:
    """Stub for corn seasonal phase detection (future expansion)."""
    return {"months": [], "code": "none", "name": "--", "direction": "wait", "bias": 0}


def get_seasonal_alignment(direction: str, date_input=None) -> dict:
    """Stub for seasonal alignment scoring (future expansion)."""
    return {"phase": get_corn_season_phase(date_input), "aligned": None, "score": 0}


def detect_shadow_signal(bar: dict, atr: float, options: dict | None = None) -> dict:
    """Detect long-shadow reversal candle patterns."""
    opts = options or {}
    o = float(bar.get("open", 0))
    h = float(bar.get("high", 0))
    lo = float(bar.get("low", 0))
    c = float(bar.get("close", 0))

    if not all(math.isfinite(v) for v in (o, h, lo, c)):
        return {"longLower": False, "longUpper": False, "lowerShadow": 0, "upperShadow": 0, "body": 0}

    body = abs(c - o)
    lower_shadow = min(o, c) - lo
    upper_shadow = h - max(o, c)
    safe_atr = atr if atr > 0 else max(h - lo, 1)
    min_shadow = safe_atr * opts.get("shadowAtrMultiplier", 0.75)
    body_limit = max(safe_atr * opts.get("bodyAtrMultiplier", 0.45), 0.5)
    shadow_body_ratio = opts.get("shadowBodyRatio", 1.8)

    return {
        "lowerShadow": lower_shadow,
        "upperShadow": upper_shadow,
        "body": body,
        "longLower": (lower_shadow >= min_shadow and lower_shadow >= body * shadow_body_ratio
                      and body <= body_limit and c >= o),
        "longUpper": (upper_shadow >= min_shadow and upper_shadow >= body * shadow_body_ratio
                      and body <= body_limit and c <= o),
    }


def build_stats_map(items: list[dict], key_getter) -> dict:
    result: dict = {}
    for item in (items or []):
        key = key_getter(item)
        if not key:
            continue
        if key not in result:
            result[key] = {"count": 0, "wins": 0, "pnl": 0}
        result[key]["count"] += 1
        if (item.get("pnl") or 0) > 0:
            result[key]["wins"] += 1
        result[key]["pnl"] += item.get("pnl") or 0
    return result
