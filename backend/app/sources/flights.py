"""Scheduled flights between two airports, from AeroDataBox (free Basic plan via RapidAPI).

AeroDataBox has no route search, only airport departure boards ("FIDS") for a local time
range of up to 12 hours. So a search lists departures from the origin airport after the
chosen time and keeps the ones landing at the destination. Connections are the same
search run again from the connection airport, after the first flight lands.

The free plan has a small monthly quota, so results are cached for 30 minutes.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta

import httpx

from .. import http
from ..config import AERODATABOX_API_KEY

HOST = "aerodatabox.p.rapidapi.com"
WINDOW_H = 12  # the longest range one FIDS request may cover


class LookupUnavailable(RuntimeError):
    """Flight lookup can't be used right now; the message is shown to the user as-is."""


def enabled() -> bool:
    return bool(AERODATABOX_API_KEY)


def _local(t) -> str | None:
    """'2026-11-02 09:00-08:00' / ISO / {'local': ...} -> '2026-11-02T09:00' (the airport's own clock)."""
    if isinstance(t, dict):
        t = t.get("local")
    if not t:
        return None
    s = str(t).strip().replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def parse_departures(data: dict, dep_iata: str) -> list[dict]:
    """FIDS departures -> [{flight, airline, dep_iata, arr_iata, dep_local, arr_local}]."""
    out = []
    for f in (data or {}).get("departures") or []:
        dep, arr = f.get("departure") or {}, f.get("arrival") or {}
        arr_iata = ((arr.get("airport") or {}).get("iata") or "").upper()
        # field names differ between API versions: scheduledTime.local vs scheduledTimeLocal
        dep_local = _local(dep.get("scheduledTime") or dep.get("scheduledTimeLocal"))
        arr_local = _local(arr.get("scheduledTime") or arr.get("scheduledTimeLocal"))
        number = (f.get("number") or "").strip()
        status = (f.get("status") or "").lower()
        if not (number and arr_iata and dep_local and arr_local) or status in ("canceled", "cancelled"):
            continue
        out.append({"flight": number, "airline": (f.get("airline") or {}).get("name") or "",
                    "dep_iata": dep_iata.upper(), "arr_iata": arr_iata,
                    "dep_local": dep_local, "arr_local": arr_local})
    return out


def normalize_number(text: str) -> str | None:
    """'wn3872' / 'WN  3872' / 'b6 15' -> 'WN 3872' / 'B6 15': airline code, a space, the number."""
    s = re.sub(r"\s+", "", text or "").upper()
    m = re.fullmatch(r"([A-Z]{3}|[A-Z0-9]{2})(\d{1,4}[A-Z]?)", s)  # IATA (2) or ICAO (3) airline code
    if not m or not re.search(r"[A-Z]", m.group(1)):
        return None
    return f"{m.group(1)} {m.group(2)}"


async def _get(url: str, params: dict) -> str:
    """One AeroDataBox call (cached 30 min) with plain-language errors; returns the raw body."""
    if not enabled():
        raise LookupUnavailable("Flight lookup isn't set up (add AERODATABOX_API_KEY to backend/.env). "
                                "You can still enter your flights by hand.")
    try:
        return await http.get(url, params=params,
                              headers={"X-RapidAPI-Key": AERODATABOX_API_KEY, "X-RapidAPI-Host": HOST},
                              ttl=1800, timeout=15, retries=0, as_json=False)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code == 429:
            raise LookupUnavailable("The free flight-lookup quota is used up for now. Enter your flights by hand.") from None
        if code in (401, 403):
            raise LookupUnavailable("The AeroDataBox key was rejected. Check AERODATABOX_API_KEY and that the "
                                    "free Basic plan is subscribed on RapidAPI.") from None
        if code in (400, 404):
            return ""  # nothing for this airport/flight/date (e.g. too far ahead)
        raise
    except (httpx.TimeoutException, httpx.ConnectError):
        raise LookupUnavailable("The flight lookup didn't respond. Try again, or enter your flights by hand.") from None


async def departures(dep_iata: str, start_local: datetime) -> list[dict]:
    """Scheduled departures from dep_iata in the WINDOW_H hours after start_local (airport time)."""
    end = start_local + timedelta(hours=WINDOW_H) - timedelta(minutes=1)
    url = f"https://{HOST}/flights/airports/iata/{dep_iata.upper()}/{start_local:%Y-%m-%dT%H:%M}/{end:%Y-%m-%dT%H:%M}"
    data = await _get(url, {"direction": "Departure", "withLeg": "true", "withCancelled": "false",
                            "withCodeshared": "false", "withCargo": "false", "withPrivate": "false",
                            "withLocation": "false"})
    if not data.strip():  # 204 No Content: no flights in that range
        return []
    return parse_departures(json.loads(data), dep_iata)


def parse_flight(data: list) -> list[dict]:
    """Flight-status records -> its legs [{flight, airline, dep_iata, arr_iata, dep_local, arr_local}]."""
    out = []
    for f in data or []:
        dep, arr = f.get("departure") or {}, f.get("arrival") or {}
        leg = {"flight": (f.get("number") or "").strip(), "airline": (f.get("airline") or {}).get("name") or "",
               "dep_iata": ((dep.get("airport") or {}).get("iata") or "").upper(),
               "arr_iata": ((arr.get("airport") or {}).get("iata") or "").upper(),
               "dep_local": _local(dep.get("scheduledTime") or dep.get("scheduledTimeLocal")),
               "arr_local": _local(arr.get("scheduledTime") or arr.get("scheduledTimeLocal"))}
        if leg["dep_iata"] and leg["arr_iata"] and leg["dep_local"]:
            out.append(leg)
    return sorted(out, key=lambda x: x["dep_local"])


async def by_number(number: str, d: date) -> list[dict]:
    """The legs a flight number flies on a date (local departure date). One request."""
    url = f"https://{HOST}/flights/number/{number.replace(' ', '')}/{d.isoformat()}"
    data = await _get(url, {"withAircraftImage": "false", "withLocation": "false", "dateLocalRole": "Departure"})
    return parse_flight(json.loads(data)) if data.strip() else []


async def search(starts: dict[str, datetime], arr_iatas: list[str], limit: int = 15) -> list[dict]:
    """Flights from the airports in `starts` to any of arr_iatas, each departing in the 12 h after
    that airport's start time (local), e.g. the earliest the traveller can be there."""
    wanted = {a.upper() for a in arr_iatas}
    # one request per origin airport (quota), all at once: each departure board takes seconds
    boards = await asyncio.gather(*[departures(dep, start) for dep, start in starts.items()])
    found = [f for board in boards for f in board if f["arr_iata"] in wanted]
    found.sort(key=lambda f: f["dep_local"])
    seen, out = set(), []
    for f in found:
        if f["flight"] not in seen:
            seen.add(f["flight"])
            out.append(f)
    return out[:limit]
