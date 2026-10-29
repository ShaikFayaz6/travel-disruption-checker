"""Resolve free-text US places ("Denton, TX", "NYC", "SFO") to coordinates.

Uses the free Open-Meteo geocoding API (no key). A 3-letter input that matches a US
commercial airport is treated as that airport.
"""
from __future__ import annotations

import re

from .. import http
from ..models import Place
from . import airports

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico",
}
STATE_ABBR = {v.lower(): k for k, v in US_STATES.items()}

ALIASES = {
    "nyc": "New York, NY", "new york city": "New York, NY", "sf": "San Francisco, CA",
    "la": "Los Angeles, CA", "dc": "Washington, DC", "washington dc": "Washington, DC",
    "washington d.c.": "Washington, DC", "philly": "Philadelphia, PA", "vegas": "Las Vegas, NV",
}


class PlaceNotFound(ValueError):
    pass


def _split(query: str) -> tuple[str, str | None]:
    q = ALIASES.get(query.strip().lower(), query.strip())
    parts = [p.strip() for p in q.split(",") if p.strip()]
    city = parts[0]
    state = None
    if len(parts) > 1:
        s = parts[1].replace(".", "").strip()
        s = re.sub(r"\s+(usa|us|united states)$", "", s, flags=re.I)
        state = s.upper() if s.upper() in US_STATES else STATE_ABBR.get(s.lower())
    return city, state


async def resolve(query: str) -> Place:
    from . import places  # local import: places imports this module

    raw = query.strip()
    if re.fullmatch(r"[A-Za-z]{3}", raw):
        ap = await airports.by_iata(raw)
        if ap:
            try:
                tz = await places.nearest_timezone(ap.lat, ap.lon)
            except Exception:  # city list unavailable; times fall back to a longitude-based offset
                tz = None
            return Place(query=raw, name=f"{ap.name} ({ap.iata})", state=ap.state, lat=ap.lat, lon=ap.lon,
                         timezone=tz)

    city, state = _split(raw)
    if state:
        try:
            hit = await places.lookup(city, state)
        except Exception:  # city list unavailable; fall back to the geocoding API
            hit = None
        if hit:
            return Place(query=raw, name=hit.label, state=hit.state, lat=hit.lat, lon=hit.lon,
                         timezone=hit.timezone)

    data = await http.get(
        GEOCODE_URL,
        params={"name": city, "count": 20, "language": "en", "format": "json", "countryCode": "US"},
        ttl=24 * 3600,
    )
    results = [r for r in (data.get("results") or []) if r.get("country_code") == "US"]
    if state:
        full = US_STATES[state].lower()
        results = [r for r in results if (r.get("admin1") or "").lower() == full]
    if not results:
        raise PlaceNotFound(f"Couldn't find a US place matching '{query}'. Try 'City, ST' or an airport code.")

    best = max(results, key=lambda r: r.get("population") or 0)
    st = STATE_ABBR.get((best.get("admin1") or "").lower())
    return Place(
        query=raw,
        name=f"{best['name']}, {st}" if st else best["name"],
        state=st,
        lat=float(best["latitude"]),
        lon=float(best["longitude"]),
        timezone=best.get("timezone"),
    )
