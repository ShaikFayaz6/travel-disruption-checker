"""Departure time -> the time window in which the traveller is actually at each place.

Without a departure time every place gets the whole travel day (local midnight to midnight).
With one, the trip is laid out on a clock: an estimated nonstop flight time or driving
time gives the arrival, and each place gets the hours the traveller is there.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .timeutil import day_window_utc, tzinfo_for

FLY_MPH, FLY_OVERHEAD_H = 470, 0.6  # cruise speed + taxi/climb/approach for a nonstop flight
ROAD_FACTOR, DRIVE_MPH = 1.2, 62  # roads ~20% longer than the straight line; mostly interstate
DRIVE_H_PER_DAY, OVERNIGHT_H = 10, 14  # long drives: up to 10 h at the wheel, then an overnight stop

CHECKIN_H = 1.0  # at the airport at least this long before take-off (bag drop, security)
BAGS_H = 0.5  # deplaning and baggage claim after landing
GROUND_MPH = 50  # to/from the airport: local roads and city traffic
GROUND_MIN_MILES = 10  # closer than this, the airport's own weather covers the drive

H = timedelta(hours=1)
Window = tuple[datetime, datetime]


@dataclass
class Plan:
    windows: dict[str, Window]  # by weather-point key ("o", "r1", ..., "d")
    departure: datetime | None = None  # UTC
    arrival: datetime | None = None  # UTC, estimated
    hours_until: float | None = None  # from now until departure
    stops: int = 0  # overnight stops on a long drive
    moving_hours: float = 0.0  # in the air or at the wheel, excluding stops

    @property
    def timed(self) -> bool:
        return self.departure is not None


def duration_hours(mode: str, dist_miles: float) -> float:
    """Hours in the air (nonstop) or at the wheel."""
    if mode == "fly":
        return FLY_OVERHEAD_H + dist_miles / FLY_MPH
    return dist_miles * ROAD_FACTOR / DRIVE_MPH


def overnight_stops(mode: str, moving_h: float) -> int:
    """Overnight stops needed before completing moving_h hours of driving (none at the very end)."""
    return 0 if mode != "drive" else max(0, math.ceil(moving_h / DRIVE_H_PER_DAY - 1e-9) - 1)


def elapsed_hours(mode: str, moving_h: float) -> float:
    """Clock time to complete moving_h hours of flying/driving, including overnight stops."""
    return moving_h + overnight_stops(mode, moving_h) * OVERNIGHT_H


def build(d: date, dep: time | None, mode: str, dist_miles: float,
          points: dict[str, tuple[float, str | None, float]], now: datetime | None = None) -> Plan:
    """points: key -> (fraction of the trip at which it's reached, tz name, lon)."""
    if dep is None:
        return Plan({k: day_window_utc(d, tz, lon) for k, (_, tz, lon) in points.items()})

    _, otz, olon = points["o"]
    start = datetime.combine(d, dep, tzinfo=tzinfo_for(otz, olon)).astimezone(timezone.utc)
    hours = duration_hours(mode, dist_miles)
    windows: dict[str, Window] = {}
    for k, (frac, _, _) in points.items():
        t = start + timedelta(hours=elapsed_hours(mode, hours * frac))
        if mode == "fly":
            # origin: getting to the airport, security, inbound-aircraft delays, departure queue;
            # destination: arrival restrictions and the trip out of the airport
            windows[k] = (t - 3 * H, t + H) if frac == 0 else (t - H, t + 2 * H)
        else:
            windows[k] = (t - H, t + 1.5 * H)
    now = now or datetime.now(timezone.utc)
    return Plan(windows, start, start + timedelta(hours=elapsed_hours(mode, hours)),
                (start - now).total_seconds() / 3600, overnight_stops(mode, hours), hours)


def from_legs(legs: list[tuple[datetime, datetime]], keys: list[str], now: datetime | None = None) -> Plan:
    """Windows from real flights. legs: (departure, arrival) in UTC, in order; keys: point key of
    each airport in order (origin, connections..., destination), so len(keys) == len(legs) + 1.

    Origin: 3 h before departure to 1 h after. Connection: 1 h before landing to 1 h after the next
    departure (the whole layover). Destination: 1 h before landing to 2 h after.
    """
    windows = {keys[0]: (legs[0][0] - 3 * H, legs[0][0] + H)}
    for i in range(1, len(legs)):
        windows[keys[i]] = (legs[i - 1][1] - H, legs[i][0] + H)
    windows[keys[-1]] = (legs[-1][1] - H, legs[-1][1] + 2 * H)
    now = now or datetime.now(timezone.utc)
    moving = sum((a - d).total_seconds() for d, a in legs) / 3600
    return Plan(windows, legs[0][0], legs[-1][1], (legs[0][0] - now).total_seconds() / 3600, 0, moving)


def ground_hours(miles: float) -> float:
    """Drive between a city and its airport, rounded up to 5 minutes (at least 15; none from the airport itself)."""
    if miles < 1:
        return 0.0
    return max(0.25, math.ceil(miles * ROAD_FACTOR / GROUND_MPH * 12) / 12)


def air_door_hours(dist_miles: float, to_miles: float, from_miles: float) -> float:
    """Door to door by air with no flight chosen: the drives, check-in, the nonstop flight and bags."""
    return (ground_hours(to_miles) + CHECKIN_H + duration_hours("fly", dist_miles) + BAGS_H
            + ground_hours(from_miles))


def drive_door_hours(dist_miles: float) -> float:
    """Door to door by car, including any overnight stops."""
    return elapsed_hours("drive", duration_hours("drive", dist_miles))


@dataclass
class Ground:
    """Door-to-door times around the flights (UTC), and the drive windows at each end."""
    leave_home: datetime
    at_airport: datetime  # CHECKIN_H before take-off
    out_of_airport: datetime  # BAGS_H after landing
    at_door: datetime
    to_window: Window
    from_window: Window


def ground(plan: Plan, to_h: float, from_h: float) -> Ground:
    at_airport = plan.departure - timedelta(hours=CHECKIN_H)
    leave = at_airport - timedelta(hours=to_h)
    out = plan.arrival + timedelta(hours=BAGS_H)
    door = out + timedelta(hours=from_h)
    return Ground(leave, at_airport, out, door, (leave - 0.5 * H, at_airport), (out, door + 0.5 * H))


def shift(plan: Plan, hours: float) -> dict[str, Window]:
    """The plan's windows if the whole trip moved by `hours`."""
    d = timedelta(hours=hours)
    return {k: (s + d, e + d) for k, (s, e) in plan.windows.items()}


def clock(dt: datetime, tz: str | None, lon: float) -> str:
    """'7:30 AM CDT' in the place's local time."""
    l = dt.astimezone(tzinfo_for(tz, lon))
    return f"{l.hour % 12 or 12}:{l.minute:02d} {'AM' if l.hour < 12 else 'PM'} {l.tzname()}"


def window_label(w: Window, tz: str | None, lon: float) -> str:
    """'Thu Oct 29, 4:30 AM–8:30 AM CDT', or 'Wed Nov 4, 9:54 PM – Thu 12:24 AM EST' across midnight."""
    s, e = (x.astimezone(tzinfo_for(tz, lon)) for x in w)
    s_txt = clock(w[0], tz, lon).rsplit(" ", 1)[0]  # drop the zone; it's on the end time
    if e.date() == s.date():
        return f"{s:%a %b} {s.day}, {s_txt}–{clock(w[1], tz, lon)}"
    return f"{s:%a %b} {s.day}, {s_txt} – {e:%a} {clock(w[1], tz, lon)}"
