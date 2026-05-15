"""Trading-session awareness for CN commodity futures.

Used to gate CTP ``send_order`` calls: the gateway rejects every order
with error code ``-1`` outside the current trading session (see logs like
"委托请求发送失败，错误代码：-1"), so we keep the request locally until the
next session opens instead.

The session table intentionally follows the generic CN commodity schedule
shared by DCE / CZCE / SHFE agricultural and industrial products:

    09:00–10:15    早盘前段
    10:30–11:30    早盘后段
    13:30–15:00    午盘
    21:00–<close>  夜盘 (close depends on product)

Night-close times come from :mod:`finme_quant.strategy.chanlun_combo`:

    basic     → 23:00   (most agri / ferrous / chems incl. SR, C, M)
    extended  → 23:30   (BU, RU)
    late      → 01:00   (CU, AL, ZN, NI, SC)
    precious  → 02:30   (AU, AG)
    none      → no night session (stock index futures, rice, etc.)

Weekends: night session from Friday evening rolls over into Saturday for
``late`` / ``precious`` groups (e.g. AU until Sat 02:30). After that the
market is fully closed until Monday 09:00.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional

try:  # relative when imported as ``trading.trading_hours``
    from ..strategy.chanlun_combo import NIGHT_SESSION_PRODUCTS
except ImportError:  # absolute fallback for scripts / tests
    from strategy.chanlun_combo import NIGHT_SESSION_PRODUCTS


# ---------------------------------------------------------------------------
# Day sessions (common to every commodity covered here)
# ---------------------------------------------------------------------------

_DAY_SESSIONS: list[tuple[time, time]] = [
    (time(9, 0),  time(10, 15)),
    (time(10, 30), time(11, 30)),
    (time(13, 30), time(15, 0)),
]

# Financial futures (CFFEX) day session closes at 15:15 instead of 15:00.
_CFFEX_DAY_SESSIONS: list[tuple[time, time]] = [
    (time(9, 30),  time(11, 30)),
    (time(13, 0),  time(15, 15)),
]

_CFFEX_PREFIXES = {"IF", "IH", "IC", "IM", "T", "TF", "TS", "TL"}


# ---------------------------------------------------------------------------
# Night-close lookup (ported from chanlun_combo.NIGHT_SESSION_PRODUCTS)
# ---------------------------------------------------------------------------

def _night_close_hhmm(prefix: str) -> Optional[int]:
    prefix = (prefix or "").upper()
    for _group, info in NIGHT_SESSION_PRODUCTS.items():
        if prefix in info["products"]:
            return info["close"]
    return None


def _hhmm_to_time(hhmm: int) -> time:
    hh, mm = divmod(int(hhmm), 100)
    return time(hh % 24, mm)


def _day_sessions_for(prefix: str) -> list[tuple[time, time]]:
    if (prefix or "").upper() in _CFFEX_PREFIXES:
        return _CFFEX_DAY_SESSIONS
    return _DAY_SESSIONS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_trading_now(prefix: str, now: Optional[datetime] = None) -> bool:
    """Return True if CTP will accept a ``send_order`` for ``prefix`` now."""
    if now is None:
        now = datetime.now()
    return _current_session_end(prefix, now) is not None


def next_session_open(prefix: str, now: Optional[datetime] = None) -> datetime:
    """Return the next datetime at which the market opens for ``prefix``."""
    if now is None:
        now = datetime.now()

    # If we're currently in a session, "next open" is effectively now.
    if is_trading_now(prefix, now):
        return now

    candidates: list[datetime] = []
    for day_offset in range(0, 4):   # today + next 3 days
        probe_day = (now + timedelta(days=day_offset)).date()
        for start, _end in _all_sessions_on(prefix, datetime.combine(probe_day, time(0, 0))):
            if start > now:
                candidates.append(start)
    candidates.sort()
    if candidates:
        return candidates[0]
    # Fallback (should not happen): +1 day 09:00
    return datetime.combine(now.date() + timedelta(days=1), time(9, 0))


def seconds_until_open(prefix: str, now: Optional[datetime] = None) -> float:
    if now is None:
        now = datetime.now()
    if is_trading_now(prefix, now):
        return 0.0
    nxt = next_session_open(prefix, now)
    return max(0.0, (nxt - now).total_seconds())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _current_session_end(prefix: str, now: datetime) -> Optional[datetime]:
    """If ``now`` is inside an active session, return that session's end."""
    today = now.date()
    yesterday = today - timedelta(days=1)

    # Check day sessions on today.
    if _is_weekday(today):
        for start, end in _day_sessions_for(prefix):
            s_dt = datetime.combine(today, start)
            e_dt = datetime.combine(today, end)
            if s_dt <= now < e_dt:
                return e_dt

    # Night session starting yesterday evening (Mon–Fri 21:00 onward).
    night_close = _night_close_hhmm(prefix)
    if night_close is not None:
        if _is_weekday(yesterday) and _crosses_midnight(night_close):
            s_dt = datetime.combine(yesterday, time(21, 0))
            e_dt = datetime.combine(today, _hhmm_to_time(night_close))
            if s_dt <= now < e_dt:
                return e_dt

        # Night session starting today evening.
        if _is_weekday(today):
            if _crosses_midnight(night_close):
                s_dt = datetime.combine(today, time(21, 0))
                e_dt = datetime.combine(today + timedelta(days=1),
                                         _hhmm_to_time(night_close))
            else:
                s_dt = datetime.combine(today, time(21, 0))
                e_dt = datetime.combine(today, _hhmm_to_time(night_close))
            if s_dt <= now < e_dt:
                return e_dt

    return None


def _all_sessions_on(prefix: str, day_anchor: datetime) -> list[tuple[datetime, datetime]]:
    """Return every session that *starts* on the anchor's calendar day."""
    day = day_anchor.date()
    sessions: list[tuple[datetime, datetime]] = []

    if _is_weekday(day):
        for start, end in _day_sessions_for(prefix):
            sessions.append((datetime.combine(day, start),
                             datetime.combine(day, end)))

    night_close = _night_close_hhmm(prefix)
    if night_close is not None and _is_weekday(day):
        s_dt = datetime.combine(day, time(21, 0))
        if _crosses_midnight(night_close):
            e_dt = datetime.combine(day + timedelta(days=1),
                                     _hhmm_to_time(night_close))
        else:
            e_dt = datetime.combine(day, _hhmm_to_time(night_close))
        sessions.append((s_dt, e_dt))

    return sessions


def _is_weekday(d) -> bool:
    return d.weekday() < 5


def _crosses_midnight(close_hhmm: int) -> bool:
    # 2300 / 2330 stay on the same calendar day; 100 / 230 roll into next day.
    return close_hhmm < 800
