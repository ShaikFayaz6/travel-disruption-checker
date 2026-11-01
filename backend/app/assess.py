"""Orchestrates one assessment: resolve places -> gather evidence in parallel -> score -> AI."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ai, perf, scoring, timing
from .config import DRIVE_THRESHOLD_MILES
from .models import Airport, AssessRequest, AssessResponse, FasterByCar, FlightLeg, Place, Segment, Signal, SourceStatus, Timing
from .sources import airports, aviationweather, faa, geocode, news, nws, openmeteo, places
from .timeutil import today_in, tzinfo_for, utc_now_iso

SOURCE_URLS = {
    "Open-Meteo forecast": "https://open-meteo.com/",
    "National Weather Service alerts": "https://www.weather.gov/alerts",
    "National Weather Service forecast": "https://www.weather.gov/",
    "AviationWeather.gov TAF": "https://aviationweather.gov/",
    "AviationWeather.gov METAR": "https://aviationweather.gov/",
    "FAA NAS Status": "https://nasstatus.faa.gov/",
    "Google News": "https://news.google.com/",
    "GDELT news": "https://www.gdeltproject.org/",
    "OurAirports": "https://ourairports.com/data/",
    "Open-Meteo geocoding": "https://open-meteo.com/en/docs/geocoding-api",
    "Gemini (AI)": "https://ai.google.dev/",
}


MAX_AIRPORT_MILES = 150  # flights must start and end this close to the origin and destination
FIRST_ANSWER_WAIT_S = 3.0  # the quick first answer waits this long for the slow sources; the AI's answer has them all


def valid_tz(name: str | None) -> str | None:
    try:
        return name if name and ZoneInfo(name) else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


class TripError(ValueError):
    pass


@dataclass
class _Tracker:
    calls: dict[str, list[tuple[bool, int, str]]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    no_data: set[str] = field(default_factory=set)

    async def run(self, name: str, coro: Awaitable[Any]) -> Any:
        t0 = time.perf_counter()
        try:
            res = await coro
            self.calls.setdefault(name, []).append((True, int((time.perf_counter() - t0) * 1000), ""))
            return res
        except Exception as e:  # each source fails independently; the assessment continues
            msg = f"{e.__class__.__name__}: {str(e)[:160]}"
            self.calls.setdefault(name, []).append((False, int((time.perf_counter() - t0) * 1000), msg))
            return None

    def skip(self, name: str, why: str) -> None:
        self.skipped[name] = why

    def statuses(self) -> list[SourceStatus]:
        out = []
        for name, calls in self.calls.items():
            ok = sum(1 for c in calls if c[0])
            errs = [c[2] for c in calls if not c[0]]
            status = "ok" if ok else "error"
            detail = f"{ok}/{len(calls)} requests succeeded" + (f" — {errs[0]}" if errs else "")
            if not ok and errs[0].startswith("ConnectionError: "):  # already a plain-language message
                detail = errs[0].removeprefix("ConnectionError: ")
            if name in self.no_data:
                status, detail = "no_data", "No data available for that period"
            out.append(SourceStatus(name=name, status=status, detail=detail,
                                    latency_ms=max(c[1] for c in calls), url=SOURCE_URLS.get(name)))
        for name, why in self.skipped.items():
            if name not in self.calls:
                out.append(SourceStatus(name=name, status="skipped", detail=why, url=SOURCE_URLS.get(name)))
        return out


def _route_points(o: Place, d: Place, dist: float) -> list[tuple[float, float, float]]:
    """Evenly spaced points along the straight line (~every 150 mi, max 4). Approximation of the road route."""
    n = min(4, int(dist // 150))
    return [(o.lat + (d.lat - o.lat) * i / (n + 1), o.lon + (d.lon - o.lon) * i / (n + 1), dist * i / (n + 1))
            for i in range(1, n + 1)]


async def resolve_airports(place: Place, query: str):
    if re.fullmatch(r"[A-Za-z]{3}", query.strip()):
        ap = await airports.by_iata(query)
        if ap:
            return [ap]
    return await airports.nearest(place.lat, place.lon)


async def _resolve_legs(legs: list[FlightLeg], dest: Place):
    """Airports (origin, connections..., destination), their time zones, each leg's (departure,
    arrival) in UTC, which arrivals were estimated, and the legs with blanks filled in.
    A missing arrival airport is the next flight's departure airport, or for the last flight the
    airport nearest the destination; a missing arrival time is estimated from the distance.
    Raises TripError for flights that don't fit together."""
    legs = [leg.model_copy() for leg in legs]
    for a, b in zip(legs, legs[1:]):
        if a.arr_iata is None:
            a.arr_iata = b.dep_iata
        elif a.arr_iata.upper() != b.dep_iata.upper():
            raise TripError(f"Your flights don't connect: one lands at {a.arr_iata.upper()} but the next "
                            f"leaves from {b.dep_iata.upper()}.")
    if legs[-1].arr_iata is None:
        near = await airports.nearest(dest.lat, dest.lon)
        if not near:
            raise TripError("Add the airport your last flight lands at.")
        legs[-1].arr_iata = near[0].iata
    codes = [legs[0].dep_iata] + [leg.arr_iata for leg in legs]
    aps = await asyncio.gather(*[airports.by_iata(c) for c in codes])
    for code, ap in zip(codes, aps):
        if ap is None:
            raise TripError(f"{code.upper()} isn't a US airport with scheduled flights.")
    tzs: list[str | None] = []
    for ap in aps:
        try:
            tzs.append(await places.nearest_timezone(ap.lat, ap.lon))
        except Exception:  # city list unavailable: longitude-based offset
            tzs.append(None)
    utc: list[tuple] = []
    estimated: list[bool] = []
    for i, leg in enumerate(legs):
        dep = leg.dep_local.replace(tzinfo=tzinfo_for(tzs[i], aps[i].lon)).astimezone(timezone.utc)
        if leg.arr_local is None:  # nonstop at the usual speed between the two airports
            miles = airports.haversine_miles(aps[i].lat, aps[i].lon, aps[i + 1].lat, aps[i + 1].lon)
            arr = dep + timedelta(hours=timing.duration_hours("fly", miles))
        else:
            arr = leg.arr_local.replace(tzinfo=tzinfo_for(tzs[i + 1], aps[i + 1].lon)).astimezone(timezone.utc)
        name = leg.flight or f"{aps[i].iata}→{aps[i + 1].iata}"
        if arr <= dep:
            raise TripError(f"{name} lands before it departs. Check the times (they're local to each airport).")
        if utc and dep < utc[-1][1]:
            raise TripError(f"{name} departs before the previous flight lands.")
        utc.append((dep, arr))
        estimated.append(leg.arr_local is None)
    return list(aps), tzs, utc, estimated, legs


def _check_no_flying_past(dest: Place, conn_aps: list, final, legs: list[FlightLeg], dest_aps: list) -> None:
    """A connection at one of the destination's own airports, then a flight on to a farther one
    (e.g. to Denver: SLC → DEN, then on to COS and a drive back), is almost surely an extra flight."""
    own = {a.iata for a in dest_aps}
    final_mi = airports.haversine_miles(dest.lat, dest.lon, final.lat, final.lon)
    for k, ap in enumerate(conn_aps):
        if ap.iata in own and airports.haversine_miles(dest.lat, dest.lon, ap.lat, ap.lon) < final_mi:
            landing, onward = legs[k].flight or "Your flight", legs[k + 1].flight or "the next flight"
            raise TripError(f"{landing} already lands at {ap.iata}, the airport for {dest.name}, but {onward} "
                            f"then takes you on to {final.iata}, {final_mi:.0f} miles away. Remove the flights "
                            f"after {ap.iata}, or check the destination.")


NEWS_COOLDOWN_S = 600  # a source that failed is skipped this long (cloud hosts often get 503s or no answer)
_news_down: dict[str, float] = {}  # source -> time.monotonic() when it can be tried again


async def _news_source(tr: _Tracker, name: str, coro: Awaitable[list[dict]]) -> list[dict] | None:
    """A source that just failed isn't tried again on every check."""
    if _news_down.get(name, 0) > time.monotonic():
        coro.close()
        tr.skip(name, "Didn't answer on a recent check; skipped for a few minutes")
        return None
    articles = await tr.run(name, coro)
    if articles is None:
        _news_down[name] = time.monotonic() + NEWS_COOLDOWN_S
    return articles


async def _news(tr: _Tracker, origin: Place, dest: Place, origin_aps: list, dest_aps: list):
    """Google News first; GDELT only if Google fails (GDELT's rate limit is strict)."""
    cities = [origin.name.split(",")[0], dest.name.split(",")[0]]
    articles = await _news_source(tr, "Google News", news.google_search(origin_aps, dest_aps, cities))
    if articles is not None:
        tr.skip("GDELT news", "Backup news source; Google News answered")
        return articles, "Google News"
    articles = await _news_source(tr, "GDELT news", news.search(origin, dest, origin_aps, dest_aps))
    return (articles, "GDELT news") if articles is not None else None


def _duration(hours: float) -> str:
    """1.25 -> '1 h 15 min', 0.75 -> '45 min'."""
    h, m = divmod(round(hours * 60), 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


SHIFTS_H = sorted(range(-6, 7), key=lambda s: (abs(s), s))  # nearest first; earlier wins a tie


def _better_departure(plan: timing.Plan, gnd: timing.Ground | None, forecasts: dict[str, tuple[str, dict]],
                      point_mode: dict[str, str], mode: str) -> tuple[int, list[str]] | None:
    """When the hourly forecast puts moderate or worse weather in the travel windows, the nearest
    departure (within 6 h, not yet past) whose windows avoid it. forecasts: key -> (label, Open-Meteo data).
    Returns (hours to move, what the current time runs into)."""
    def risk(windows):
        worst, total, why = 0, 0, []
        for key, (label, data) in forecasts.items():
            r = openmeteo.window_risk(data, windows[key], point_mode.get(key, mode))
            if r is None:  # beyond the forecast: can't compare
                return None
            worst, total = max(worst, r[0]), total + r[0]
            why += [f"{t} at {label}" for t in r[1]]
        return worst, total, why

    now = risk(plan.windows)
    if not now or now[0] < 2:
        return None
    start = gnd.leave_home if gnd else plan.departure - timedelta(hours=1)
    lead_h = (start - datetime.now(timezone.utc)).total_seconds() / 3600  # time left to get going
    best = None
    for s in SHIFTS_H:
        if s == 0 or lead_h + s < 0.5:
            continue
        r = risk(timing.shift(plan, s))
        if r and r[0] < now[0] and (best is None or r[1] < best[1][1]):
            best = (s, r)
    return (best[0], now[2][:2]) if best else None


def _timing(plan: timing.Plan, mode: str, origin: Place, dest: Place, points: list, win_text: dict,
            legs_text: list[str] | None = None, view: str | None = None) -> Timing:
    """view: the viewer's time zone, if every time is shown in it (else each place's own)."""
    otz, dtz = ((view, 0), (view, 0)) if view else ((origin.timezone, origin.lon), (dest.timezone, dest.lon))
    dep = timing.clock(plan.departure, *otz)
    arr = timing.clock(plan.arrival, *dtz)
    extra = (plan.arrival.astimezone(tzinfo_for(*dtz)).date() - plan.departure.astimezone(tzinfo_for(*otz)).date()).days
    if extra > 0:
        arr += f" (+{extra} day{'s' if extra > 1 else ''})"
    if legs_text:
        basis = ("Times are from your flights. The departure airport is checked from 3 hours before take-off "
                 "to 1 hour after; each connection from 1 hour before you land until 1 hour after your next "
                 "flight leaves; the destination from 1 hour before landing to 2 hours after.")
    elif mode == "fly":
        basis = ("No flight chosen, so the flight is assumed to leave once you've driven to the airport and "
                 f"spent {timing.CHECKIN_H:.0f} hour there. Arrival estimated for a nonstop flight (~{timing.FLY_MPH} mph plus about "
                 f"{timing.FLY_OVERHEAD_H * 60:.0f} min for taxi, climb and approach); connections add time. "
                 "The origin is checked from 3 hours before departure (getting to the airport, inbound "
                 "aircraft) to 1 hour after; the destination from 1 hour before arrival to 2 hours after.")
    else:
        stops = (f" It's about {plan.moving_hours:.0f} hours at the wheel, so this assumes up to {timing.DRIVE_H_PER_DAY} "
                 f"hours of driving a day with {plan.stops} overnight stop{'s' if plan.stops != 1 else ''} "
                 f"of {timing.OVERNIGHT_H} hours." if plan.stops else " No stops assumed.")
        basis = (f"Arrival estimated at {timing.DRIVE_MPH} mph over roads about "
                 f"{(timing.ROAD_FACTOR - 1) * 100:.0f}% longer than the straight line.{stops} "
                 "Each point is checked from 1 hour before to 1.5 hours after you're expected to pass it.")
    return Timing(departure=dep, est_arrival=arr,
                  duration_hours=round((plan.arrival - plan.departure).total_seconds() / 3600, 1),
                  moving_hours=round(plan.moving_hours, 1), overnight_stops=plan.stops,
                  hours_until_departure=round(plan.hours_until, 1),
                  windows={label: win_text[key] for key, _, label, _, _ in points}, basis=basis,
                  from_flights=bool(legs_text), legs=legs_text or [])


async def assess_stream(req: AssessRequest) -> AsyncIterator[AssessResponse]:
    """Yields the assessment: with the AI on, first a rules-only one (ai_pending), then the AI one."""
    tr = _Tracker()
    legs = req.legs or []
    if legs:  # the first flight sets the travel date and departure time
        req = req.model_copy(update={"date": legs[0].dep_local.date(), "time": legs[0].dep_local.time(),
                                     "mode": "fly"})

    # 1. Resolve places
    try:
        with perf.step("Resolve places (geocoding)"):
            origin, dest = await asyncio.gather(geocode.resolve(req.origin), geocode.resolve(req.destination))
    except geocode.PlaceNotFound as e:
        raise TripError(str(e)) from e
    except Exception as e:  # network failure on geocoding / airport list
        raise TripError(f"Couldn't look up the locations right now ({e.__class__.__name__}). "
                        "Check the backend's internet access and try again.") from e
    tr.calls["Open-Meteo geocoding"] = [(True, 0, "")]

    # times shown in the viewer's zone, and a typed date+time read in it (flights keep their airports' clocks)
    view = valid_tz(req.display_tz)
    if view and req.time and not legs:
        t = datetime.combine(req.date, req.time, tzinfo=ZoneInfo(view)).astimezone(tzinfo_for(origin.timezone, origin.lon))
        req = req.model_copy(update={"date": t.date(), "time": t.time().replace(second=0, microsecond=0)})

    def shown(tz_lon: tuple) -> tuple:
        """The clock to show a time on: the viewer's, else the place's own."""
        return (view, 0) if view else tz_lon

    days_out = (req.date - today_in(origin.timezone, origin.lon)).days
    if days_out < 0:
        raise TripError("The travel date is in the past.")

    dist = airports.haversine_miles(origin.lat, origin.lon, dest.lat, dest.lon)
    mode = req.mode if req.mode != "auto" else ("drive" if dist < DRIVE_THRESHOLD_MILES else "fly")

    origin_aps, dest_aps, conn_aps = [], [], []  # conn_aps: connection airports, in order
    leg_utc: list[tuple] = []
    if legs:  # exactly the airports on the traveller's flights
        try:
            leg_aps, leg_tzs, leg_utc, arr_estimated, legs = await _resolve_legs(legs, dest)
        except TripError:
            raise
        except Exception as e:  # airport list unavailable
            raise TripError(f"Couldn't check your flights' airports right now ({e.__class__.__name__}).") from e
        origin_aps, conn_aps, dest_aps = leg_aps[:1], leg_aps[1:-1], leg_aps[-1:]
        tr.calls["OurAirports"] = [(True, 0, "")]
        _check_no_flying_past(dest, conn_aps, dest_aps[0], legs, await airports.nearest(dest.lat, dest.lon))
        for place, ap, end in ((origin, origin_aps[0], "first flight leaves from"), (dest, dest_aps[0], "last flight lands at")):
            miles = airports.haversine_miles(place.lat, place.lon, ap.lat, ap.lon)
            if miles > MAX_AIRPORT_MILES:  # e.g. only the first flight of a connection was given
                raise TripError(f"Your {end} {ap.iata}, {miles:.0f} miles from {place.name}. "
                                + ("Add your next flight." if place is dest else "Check the first flight."))
    elif mode == "fly":
        with perf.step("Nearest airports (OurAirports)"):
            res = await tr.run("OurAirports", asyncio.gather(resolve_airports(origin, req.origin),
                                                             resolve_airports(dest, req.destination)))
        if res is None:
            raise TripError("Couldn't load the airport database (OurAirports). Check network access and retry.")
        origin_aps, dest_aps = res
    else:
        tr.skip("OurAirports", "Driving trip — airports not used")

    # 2. Weather points: the main airport on each side (flying), plus connections, or the route (driving)
    points: list[tuple[str, str, str, float, float]] = []  # (key, side, label, lat, lon)
    fracs = {"o": 0.0, "d": 1.0}  # how far through the trip each point is reached
    if legs:
        oa, da = origin_aps[0], dest_aps[0]
        points.append(("o", "origin", f"{oa.iata} ({origin.name})", oa.lat, oa.lon))
        points += [(f"c{i}", "route", f"{ap.iata} (connection)", ap.lat, ap.lon) for i, ap in enumerate(conn_aps, 1)]
        points.append(("d", "destination", f"{da.iata} ({dest.name})", da.lat, da.lon))
    elif mode == "fly":
        oa, da = origin_aps[0], dest_aps[0]
        points += [("o", "origin", f"{oa.iata} ({origin.name})", oa.lat, oa.lon),
                   ("d", "destination", f"{da.iata} ({dest.name})", da.lat, da.lon)]
    else:
        points.append(("o", "origin", origin.name, origin.lat, origin.lon))
        for i, (la, lo, mi) in enumerate(_route_points(origin, dest, dist), 1):
            try:
                town = await places.nearest_town(la, lo)
            except Exception:  # city list unavailable
                town = None
            road_mi = mi * timing.ROAD_FACTOR
            label = f"Near {town} (~{road_mi:.0f} mi in)" if town else f"En route (~{road_mi:.0f} mi from {origin.name})"
            points.append((f"r{i}", "route", label, la, lo))
            fracs[f"r{i}"] = mi / dist
        points.append(("d", "destination", dest.name, dest.lat, dest.lon))

    # 2b. Time windows: the whole travel day, or (with a departure time) the hours spent at each point
    tz_of = {"origin": origin.timezone, "destination": dest.timezone}
    route_tz = {}  # a long drive crosses time zones: each route point gets its own
    for key, side, _, la, lo in points:
        if side == "route":
            try:
                route_tz[key] = await places.nearest_timezone(la, lo)
            except Exception:  # city list unavailable: fall back to the origin's zone
                route_tz[key] = None
    tz_key = {key: (route_tz.get(key) or tz_of.get(side) or origin.timezone, lo) for key, side, _, _, lo in points}
    if legs:  # each airport's own zone (points are in the same order as the flights' airports)
        tz_key = {key: (leg_tzs[i] or tz_key[key][0], lo) for i, (key, _, _, _, lo) in enumerate(points)}
        plan = timing.from_legs(leg_utc, [key for key, *_ in points])
    else:
        dep_date, dep_time = req.date, req.time
        if mode == "fly" and req.time:  # the time is when they leave home: the flight is after the drive + check-in
            oa = origin_aps[0]
            lead = timing.CHECKIN_H + timing.ground_hours(airports.haversine_miles(origin.lat, origin.lon, oa.lat, oa.lon))
            t = datetime.combine(req.date, req.time) + timedelta(minutes=round(lead * 60))
            dep_date, dep_time = t.date(), t.time()
        plan = timing.build(dep_date, dep_time, mode, dist,
                            {key: (fracs[key], *tz_key[key]) for key, *_ in points})
    if plan.timed and plan.hours_until < -0.25:
        raise TripError("That time has already passed. Pick a later time.")

    # 2c. Flying: the drives to and from the airports, checked as drives
    ap_keys = [key for key, *_ in points]  # flying: the airports, in trip order
    point_mode: dict[str, str] = {}
    gnd, drive_to, drive_from = None, None, None
    if mode == "fly" and plan.timed:
        oa, da = origin_aps[0], dest_aps[0]
        to_mi = airports.haversine_miles(origin.lat, origin.lon, oa.lat, oa.lon)
        from_mi = airports.haversine_miles(da.lat, da.lon, dest.lat, dest.lon)
        to_h, from_h = timing.ground_hours(to_mi), timing.ground_hours(from_mi)
        gnd = timing.ground(plan, to_h, from_h)
        # every drive is shown and timed; only longer ones get their own weather check
        # (a city that is the airport itself, e.g. "BWI" typed as the destination, has no drive)
        if to_mi >= 1:
            drive_to = f"{origin.name} → {oa.iata}: about {_duration(to_h)} by car ({to_mi * timing.ROAD_FACTOR:.0f} mi)"
        if from_mi >= 1:
            drive_from = f"{da.iata} → {dest.name}: about {_duration(from_h)} by car ({from_mi * timing.ROAD_FACTOR:.0f} mi)"
        if to_mi >= timing.GROUND_MIN_MILES:
            points.insert(0, ("og", "origin", f"Drive to {oa.iata} (from {origin.name})", origin.lat, origin.lon))
            plan.windows["og"], tz_key["og"], point_mode["og"] = gnd.to_window, (origin.timezone, origin.lon), "drive"
        if from_mi >= timing.GROUND_MIN_MILES:
            points.append(("dg", "destination", f"Drive from {da.iata} (to {dest.name})", dest.lat, dest.lon))
            plan.windows["dg"], tz_key["dg"], point_mode["dg"] = gnd.from_window, (dest.timezone, dest.lon), "drive"
    win_text = {key: timing.window_label(plan.windows[key], *shown(tz_key[key])) for key, *_ in points} if plan.timed else {}

    # 2d. Flying with no flight chosen: would driving be quicker door to door?
    faster_by_car = None
    if mode == "fly" and not legs:
        air_h = timing.air_door_hours(dist, airports.haversine_miles(origin.lat, origin.lon, origin_aps[0].lat, origin_aps[0].lon),
                                      airports.haversine_miles(dest_aps[0].lat, dest_aps[0].lon, dest.lat, dest.lon))
        drive_h = timing.drive_door_hours(dist)
        if drive_h < air_h:
            faster_by_car = FasterByCar(drive_time=_duration(drive_h), air_time=_duration(air_h))
    hours = plan.hours_until

    signals: list[Signal] = []
    weather_summary: dict[str, dict] = {}
    better = None  # (hours to move the departure, what it avoids)

    if days_out <= 15:
        # windows can run past midnight or over several days (long drives): fetch hourly data that far,
        # up to the end of Open-Meteo's 16-day range
        end = None
        if plan.timed:
            last = max(w[1] for w in plan.windows.values()).astimezone(tzinfo_for(origin.timezone, origin.lon))
            end = min(last.date() + timedelta(days=1), req.date + timedelta(days=15 - days_out))
        with perf.step(f"Hourly forecasts (Open-Meteo, {len(points)} points)"):
            om = await asyncio.gather(*[tr.run("Open-Meteo forecast", openmeteo.fetch(la, lo, req.date, end))
                                        for _, _, _, la, lo in points])
        for (key, side, label, _, _), data in zip(points, om):
            if not data:
                continue
            sig, summ = openmeteo.evaluate(data, sid=f"om-{key}", side=side, label=label,
                                           window=plan.windows[key] if plan.timed else None,
                                           window_text=win_text.get(key), mode=point_mode.get(key, mode))
            signals += sig
            weather_summary[label] = summ
        if plan.timed and all(om):
            better = _better_departure(plan, gnd, {p[0]: (p[2], data) for p, data in zip(points, om)}, point_mode, mode)
    else:
        tr.skip("Open-Meteo forecast", "Travel date is beyond the 16-day forecast range")

    # 3. Everything else in parallel
    jobs: dict[str, Awaitable] = {}
    for key, side, label, la, lo in points:
        jobs[f"alerts|{key}"] = tr.run("National Weather Service alerts", nws.alerts(la, lo))
        if days_out <= 6:
            jobs[f"nwsfc|{key}"] = tr.run("National Weather Service forecast", nws.forecast_periods(la, lo))
    if days_out > 6:
        tr.skip("National Weather Service forecast", "NWS text forecasts cover the next 7 days only")

    icaos = [a.icao for a in origin_aps + conn_aps + dest_aps if a.icao]
    taf_range = hours <= 30 if plan.timed else days_out <= 1
    obs_matter = hours <= 6 if plan.timed else days_out == 0  # current conditions only matter if leaving soon
    # Slow sources (AviationWeather.gov can take 10 s; news from a cloud host 8 s when both sources time out):
    # started here, but with the AI on the first answer waits for them only FIRST_ANSWER_WAIT_S
    slow: dict[str, asyncio.Task] = {}
    if mode == "fly" and taf_range:
        slow["taf"] = asyncio.create_task(tr.run("AviationWeather.gov TAF", aviationweather.fetch("taf", icaos)))
        slow["metar"] = asyncio.create_task(tr.run("AviationWeather.gov METAR", aviationweather.fetch("metar", icaos)))
    elif mode == "fly":
        tr.skip("AviationWeather.gov TAF", "Airport forecasts (TAF) only cover the next ~30 hours")
        tr.skip("AviationWeather.gov METAR", "Current observations only matter for same-day travel")
    if mode == "fly":
        jobs["faa"] = tr.run("FAA NAS Status", faa.fetch())
    else:
        tr.skip("FAA NAS Status", "Driving trip — airport programs not relevant")
    slow["news"] = asyncio.create_task(_news(tr, origin, dest, origin_aps + conn_aps, dest_aps))

    with perf.step("In parallel: NWS, aviation weather, FAA, news"):
        deadline = time.monotonic() + FIRST_ANSWER_WAIT_S
        results = dict(zip(jobs.keys(), await asyncio.gather(*jobs.values())))
        await asyncio.wait(slow.values(), timeout=max(0.0, deadline - time.monotonic()) if ai.enabled() else None)

    def aviation_signals(done: dict[str, Any]) -> list[Signal]:
        out: list[Signal] = []
        if done.get("taf"):
            out += aviationweather.evaluate_tafs(done["taf"], origin_aps, side="origin", window=plan.windows["o"])
            for i, ap in enumerate(conn_aps, 1):
                out += aviationweather.evaluate_tafs(done["taf"], [ap], side="route", window=plan.windows[f"c{i}"])
            out += aviationweather.evaluate_tafs(done["taf"], dest_aps, side="destination", window=plan.windows["d"])
        if "taf" in done and not out:  # it failed, came back empty, or doesn't cover these hours
            tr.no_data.add("AviationWeather.gov TAF")
        if done.get("metar"):
            out += aviationweather.evaluate_metars(done["metar"], origin_aps, side="origin", same_day=obs_matter)
            out += aviationweather.evaluate_metars(done["metar"], conn_aps, side="route", same_day=obs_matter)
            out += aviationweather.evaluate_metars(done["metar"], dest_aps, side="destination", same_day=obs_matter)
        return out

    late = {k: t for k, t in slow.items() if not t.done()}  # still loading: added before the AI's answer
    results |= {k: t.result() for k, t in slow.items() if k not in late}
    loading = "Still loading; included when the AI's answer arrives"
    for k in late:
        if k == "news":  # whichever news source hasn't answered yet (_news overwrites this when it settles)
            for name in ("Google News", "GDELT news"):
                if name not in tr.calls and name not in tr.skipped:
                    tr.skip(name, loading)
        else:
            tr.skip(f"AviationWeather.gov {k.upper()}", loading)

    for key, side, label, la, lo in points:
        if results.get(f"alerts|{key}") is not None:
            signals += nws.evaluate_alerts(results[f"alerts|{key}"], sid=f"nws-{key}", side=side,
                                           label=label, window=plan.windows[key])
        if results.get(f"nwsfc|{key}"):
            signals += nws.evaluate_forecast(results[f"nwsfc|{key}"], sid=f"nwsfc-{key}", side=side,
                                             label=label, d=req.date, lat=la, lon=lo,
                                             window=plan.windows[key] if plan.timed else None)
    signals += aviation_signals(results)
    if results.get("faa"):
        try:
            signals += faa.evaluate(results["faa"], origin_aps, dest_aps, days_out=days_out, hours_until=hours,
                                    via=conn_aps)
        except Exception as e:  # malformed XML
            tr.calls["FAA NAS Status"].append((False, 0, f"Parse error: {e.__class__.__name__}"))

    legs_text = None
    if legs:  # airports and their point keys are both in trip order: origin, connections..., destination
        seq, keys = origin_aps + conn_aps + dest_aps, ap_keys
        legs_text = [f"{leg.flight or 'Flight'} · {seq[i].iata} {timing.clock(dep, *shown(tz_key[keys[i]]))} → "
                     f"{seq[i + 1].iata} {'about ' if arr_estimated[i] else ''}{timing.clock(arr, *shown(tz_key[keys[i + 1]]))}"
                     + (" (arrival estimated)" if arr_estimated[i] else "")
                     for i, (leg, (dep, arr)) in enumerate(zip(legs, leg_utc))]
    timing_out = _timing(plan, mode, origin, dest, points, win_text, legs_text, view) if plan.timed else None
    if timing_out and mode == "fly":
        mins = lambda a, b: round((b - a).total_seconds() / 60)  # noqa: E731
        if legs:
            seq = origin_aps + conn_aps + dest_aps
            timing_out.segments = [
                Segment(flight=leg.flight, dep_iata=seq[i].iata, arr_iata=seq[i + 1].iata, minutes=mins(dep, arr),
                        estimated=arr_estimated[i],
                        layover_min=mins(arr, leg_utc[i + 1][0]) if i + 1 < len(leg_utc) else None)
                for i, (leg, (dep, arr)) in enumerate(zip(legs, leg_utc))]
        else:
            timing_out.segments = [Segment(dep_iata=origin_aps[0].iata, arr_iata=dest_aps[0].iata,
                                           minutes=mins(plan.departure, plan.arrival), estimated=True)]
    if timing_out and gnd:
        otz, dtz = shown((origin.timezone, origin.lon)), shown((dest.timezone, dest.lon))
        timing_out.leave_by = timing.clock(gnd.leave_home, *otz)
        # for the form's Leave at, which is in the same clock as the times shown
        timing_out.leave_local = gnd.leave_home.astimezone(tzinfo_for(*otz)).strftime("%Y-%m-%dT%H:%M")
        timing_out.at_airport_by = timing.clock(gnd.at_airport, *shown(tz_key["o"]))
        timing_out.out_of_airport = timing.clock(gnd.out_of_airport, *shown(tz_key["d"]))
        timing_out.door_arrival = timing.clock(gnd.at_door, *dtz)
        timing_out.drive_to_airport, timing_out.drive_from_airport = drive_to, drive_from
        timing_out.drive_to_min = round(to_h * 60) if drive_to else None
        timing_out.drive_from_min = round(from_h * 60) if drive_from else None
        if not legs:  # no flight chosen: say exactly what was assumed
            oa, da = origin_aps[0], dest_aps[0]
            timing_out.est_flight = (f"{oa.iata} {timing.clock(plan.departure, *shown(tz_key['o']))} → "
                                     f"{da.iata} {timing.clock(plan.arrival, *shown(tz_key['d']))}")
            timing_out.cruise_mph, timing_out.overhead_min = timing.FLY_MPH, round(timing.FLY_OVERHEAD_H * 60)
            timing_out.flight_miles = round(dist)
        timing_out.basis += (f" Door to door: at the airport {timing.CHECKIN_H:.0f} hour before take-off for bag "
                             f"drop and security, about {timing.BAGS_H * 60:.0f} min after landing for bags, and "
                             f"drives at about {timing.GROUND_MPH} mph on roads ~20% longer than the straight line.")
    if timing_out and better:
        s, why = better
        t = timing.clock(plan.departure + timedelta(hours=s), *shown(tz_key["o"]))
        timing_out.better_departure = (
            f"Departing around {t} ({abs(s)} h {'earlier' if s < 0 else 'later'}) looks clearer in the hourly "
            f"forecast (at your time: {'; '.join(why)})." + (" Use Find flights to look for one then." if legs else ""))

    route = f"{origin.name} → {dest.name}"
    when = f"{req.date.isoformat()}" + (f", departing {timing_out.departure}" if timing_out else "")
    trip_desc = (f"{route} on {when} ({mode}); airports: "
                 f"{', '.join(a.iata for a in origin_aps) or 'n/a'} → {', '.join(a.iata for a in dest_aps) or 'n/a'}")

    # Steps 4-6 run twice when the AI is on: first with rules only, so the page can show the trip
    # in about 2 s, then with Gemini's news picks and assessment (one call, slower).
    found = signals

    async def finish(use_ai: bool) -> AssessResponse:
        articles, news_source = results.get("news") or ([], "")

        def with_news(relevant: list[dict], method: str) -> list[Signal]:
            """The evidence plus the relevant headlines, each as news-<its index in articles>."""
            out = list(found)
            for item in relevant[:8]:
                a = articles[item["index"]]
                out.append(Signal(
                    id=f"news-{item['index']}", source=news_source, category="news", side="national",
                    location=a.get("domain") or "news", severity=item["severity"],
                    title=a["title"][:200], detail=f"{item['reason']} ({method}; source: {a.get('domain')})",
                    observed_at=a.get("seendate"), valid_for="last 3 days of coverage",
                    url=a["url"] if str(a.get("url", "")).startswith(("https://", "http://")) else None,
                ))
            return out

        def keyword_news() -> list[Signal]:
            if not articles:
                return list(found)
            terms = [a.iata for a in origin_aps + dest_aps] + [origin.name.split(",")[0], dest.name.split(",")[0],
                                                                 "air traffic", "FAA", "TSA"]
            return with_news(news.keyword_relevance(articles, terms), "keyword-matched")

        def rated(signals: list[Signal]) -> dict:
            """5. Score + confidence, and the context the AI writes from."""
            pts, level = scoring.score(signals)
            note = scoring.horizon_note(days_out, mode)
            if timing_out:
                note += (f" You depart {timing_out.departure}, so weather, alerts and airport forecasts are checked for "
                         "the hours you're at each place rather than the whole day.")
                if mode == "fly" and days_out <= 1 and hours > 6:
                    note += " Live FAA programs count fully only within 6 hours of departure."
            sources = tr.statuses()
            conf, conf_reason = scoring.confidence(days_out, [s for s in sources if s.name != "Gemini (AI)"])
            context = {
                "route": route, "travel_date": req.date.isoformat(), "days_out": days_out, "mode": mode,
                "departure_time": timing_out.departure if timing_out else "not given: the whole travel day was assessed",
                **({"hours_until_departure": round(hours, 1), "estimated_arrival": timing_out.est_arrival,
                    "travel_windows": timing_out.windows, "timing_basis": timing_out.basis} if timing_out else {}),
                **({"door_to_door": {"leave_origin_by": timing_out.leave_by, "at_airport_by": timing_out.at_airport_by,
                                     "arrive_at_destination": timing_out.door_arrival, "drives": [x for x in (timing_out.drive_to_airport, timing_out.drive_from_airport) if x]}}
                   if timing_out and timing_out.leave_by else {}),
                **({"better_departure": timing_out.better_departure} if timing_out and timing_out.better_departure else {}),
                "distance_miles": round(dist), "risk_level": level, "risk_score": pts,
                "confidence": conf, "confidence_reason": conf_reason, "horizon_note": note,
                "origin_airports": [a.iata for a in origin_aps], "destination_airports": [a.iata for a in dest_aps],
                "weather_summary": weather_summary,
                "source_status": [{"name": s.name, "status": s.status, "detail": s.detail} for s in sources],
            }
            return {"pts": pts, "level": level, "note": note, "conf": conf, "conf_reason": conf_reason, "context": context}

        async def write(r: dict, signals: list[Signal], step: str) -> Assessment:
            """6. The AI's assessment from this evidence, or the rule-based one if Gemini fails."""
            with perf.step(step):
                out = await tr.run("Gemini (AI)", ai.synthesize(r["context"], signals))
            return out or ai.fallback(r["context"], signals, tr.calls["Gemini (AI)"][-1][2])

        assessment = None
        if use_ai and articles:
            # 4+6 in one Gemini call: it picks the news that matters and writes the assessment
            base = rated(found)
            with perf.step("AI: news + assessment (one Gemini call)"):
                res = await tr.run("Gemini (AI)", ai.assess_with_news(base["context"], found, trip_desc, articles))
            if res:
                picked, assessment = res
                signals = with_news(picked, "AI-classified")
                r = rated(signals)
                if r["level"] != base["level"]:  # its news changed the rule rating: the text must match it
                    assessment = await write(r, signals, "AI: rewrite for the rating with news (Gemini)")
        if assessment is None:  # no headlines, AI off or failed: keyword-matched news
            signals = keyword_news()
            r = rated(signals)
            if use_ai:
                assessment = await write(r, signals, "AI: write the assessment (Gemini)")
            elif ai.enabled():  # the quick first answer: the AI's is on its way
                assessment = ai.fallback(r["context"], signals, None)
            else:
                tr.skip("Gemini (AI)", "GEMINI_API_KEY not set — using rule-based summary")
                assessment = ai.fallback(r["context"], signals, "GEMINI_API_KEY not set")

        signals.sort(key=lambda s: (not s.applies_to_travel_date, -s.severity, s.side, s.source))
        return AssessResponse(
            request=req, origin=origin, destination=dest, mode=mode, distance_miles=round(dist, 1),
            days_out=days_out, horizon_note=r["note"], timing=timing_out, faster_by_car=faster_by_car,
            origin_airports=origin_aps, destination_airports=dest_aps,
            risk_level=r["level"], risk_score=r["pts"], confidence=r["conf"], confidence_reason=r["conf_reason"],
            assessment=assessment, signals=signals, sources=tr.statuses(), generated_at=utc_now_iso(),
            timings=perf.report(), ai_pending=ai.enabled() and not use_ai,
        )

    if ai.enabled():
        yield await finish(use_ai=False)
    if late:
        with perf.step(f"After the first answer: {', '.join(late)}"):
            await asyncio.wait(late.values())
        done = {k: t.result() for k, t in late.items()}
        results |= done  # the news is read in finish()
        found += aviation_signals(done)
    yield await finish(use_ai=ai.enabled())


async def assess(req: AssessRequest) -> AssessResponse:
    """The finished assessment (the last of assess_stream's answers)."""
    out = None
    async for out in assess_stream(req):
        pass
    return out
