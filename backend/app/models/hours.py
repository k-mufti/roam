"""Opening-hours representation and queries.

Stored shape (JSONB on `Place.hours`)::

    {"mon": [{"open": "09:00", "close": "17:00"}],
     "tue": [{"open": "09:00", "close": "14:00"},
             {"open": "17:00", "close": "23:30"}],   # siesta split, very Madrid
     "sun": []}                                       # explicitly closed

Three states, and the distinction matters to the optimizer:

* key present with intervals  -> open during them
* key present, empty list     -> **closed** that day (hard constraint)
* key absent (or `hours` NULL)-> **unknown**; treated as permissive, because
  refusing to schedule everything we lack data for would gut the itinerary.

Closing times past midnight ("23:00-03:00", ubiquitous for Madrid nightlife)
are stored with a close time numerically smaller than the open time, and the
helpers below interpret that as spilling into the next day.
"""

from __future__ import annotations

from datetime import time
from typing import Any

DAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: Google's `periods[].day` is 0=Sunday..6=Saturday; Python's weekday() is
#: 0=Monday..6=Sunday. Mixing these up is the classic hours bug.
GOOGLE_DAY_TO_KEY = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat"}


def day_key(weekday: int) -> str:
    """`datetime.weekday()` (0=Mon) -> our key."""
    return DAY_KEYS[weekday % 7]


def _to_minutes(value: str) -> int:
    hh, mm = value.split(":")
    return int(hh) * 60 + int(mm)


def minutes_to_time(minutes: int) -> time:
    minutes %= 24 * 60
    return time(hour=minutes // 60, minute=minutes % 60)


def intervals_for(hours: dict[str, Any] | None, weekday: int) -> list[tuple[int, int]] | None:
    """Open intervals as (open_min, close_min) since midnight, or None if unknown.

    A close value <= open is normalized to close+1440 so callers can compare
    linearly without special-casing overnight venues.
    """
    if not hours:
        return None
    key = day_key(weekday)
    if key not in hours:
        return None
    raw = hours.get(key) or []
    out: list[tuple[int, int]] = []
    for item in raw:
        try:
            start = _to_minutes(item["open"])
            end = _to_minutes(item["close"])
        except (KeyError, ValueError, TypeError):
            continue
        if end <= start:
            end += 24 * 60
        out.append((start, end))
    return sorted(out)


def is_open_during(
    hours: dict[str, Any] | None, weekday: int, start_min: int, end_min: int
) -> bool:
    """Whether a visit spanning [start_min, end_min) fits inside one open interval.

    Unknown hours return True — see the module docstring on why unknown is
    permissive rather than closed.
    """
    intervals = intervals_for(hours, weekday)
    if intervals is None:
        return True
    if not intervals:
        return False
    return any(o <= start_min and end_min <= c for o, c in intervals)


def earliest_open(hours: dict[str, Any] | None, weekday: int, not_before_min: int) -> int | None:
    """First minute at/after `not_before_min` the place is open, or None if it
    cannot open again that day. Used to insert waiting time in the schedule."""
    intervals = intervals_for(hours, weekday)
    if intervals is None:
        return not_before_min
    for open_min, close_min in intervals:
        if close_min <= not_before_min:
            continue
        return max(open_min, not_before_min)
    return None


def from_google_periods(periods: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Convert Google Places `regularOpeningHours.periods` to our shape."""
    if periods is None:
        return None
    out: dict[str, list[dict[str, str]]] = {k: [] for k in DAY_KEYS}
    for period in periods:
        open_spec = period.get("open") or {}
        close_spec = period.get("close")
        gday = open_spec.get("day")
        if gday is None:
            continue
        key = GOOGLE_DAY_TO_KEY.get(gday)
        if key is None:
            continue
        open_t = f"{open_spec.get('hour', 0):02d}:{open_spec.get('minute', 0):02d}"
        if close_spec is None:
            # Google omits `close` for 24h venues.
            out[key].append({"open": "00:00", "close": "23:59"})
            continue
        close_t = f"{close_spec.get('hour', 0):02d}:{close_spec.get('minute', 0):02d}"
        out[key].append({"open": open_t, "close": close_t})
    return out


def from_yelp_open(entries: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Convert Yelp's `hours[0].open` list (day 0=Monday, 'HHMM' strings)."""
    if entries is None:
        return None
    out: dict[str, list[dict[str, str]]] = {k: [] for k in DAY_KEYS}
    for entry in entries:
        day = entry.get("day")
        if day is None or not (0 <= day <= 6):
            continue
        start, end = entry.get("start", ""), entry.get("end", "")
        if len(start) != 4 or len(end) != 4:
            continue
        out[DAY_KEYS[day]].append(
            {"open": f"{start[:2]}:{start[2:]}", "close": f"{end[:2]}:{end[2:]}"}
        )
    return out
