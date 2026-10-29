"""Small time helpers: the travel day as a UTC window, parsing timestamps."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def tzinfo_for(tzname: str | None, lon: float):
    if tzname:
        try:
            return ZoneInfo(tzname)
        except ZoneInfoNotFoundError:
            pass
    # crude fallback: solar offset from longitude (good enough to place a whole-day window)
    return timezone(timedelta(hours=round(lon / 15)))


def today_in(tzname: str | None, lon: float) -> date:
    return datetime.now(tzinfo_for(tzname, lon)).date()


def day_window_utc(d: date, tzname: str | None, lon: float) -> tuple[datetime, datetime]:
    tz = tzinfo_for(tzname, lon)
    start = datetime.combine(d, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def parse_iso(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def overlaps(a_start: datetime | None, a_end: datetime | None, b_start: datetime, b_end: datetime) -> bool:
    a_start = a_start or b_start
    a_end = a_end or (a_start + timedelta(hours=12))
    return a_start < b_end and a_end > b_start


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
