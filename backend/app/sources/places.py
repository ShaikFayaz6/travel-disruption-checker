"""Type-ahead suggestions for US cities and airports.

Cities come from the free GeoNames "cities1000" dump (CC-BY 4.0): every place with
1,000+ people. The worldwide zip (~11 MB) is downloaded once; only US rows are kept,
cached under backend/data/ for 30 days. Matching is local, so suggestions are instant:
prefix matches first (biggest places first), then close spellings ("danton" -> Denton).
"""
from __future__ import annotations

import asyncio
import difflib
import io
import math
import time
import zipfile
from dataclasses import dataclass

from .. import http
from ..config import DATA_DIR
from . import airports
from .geocode import US_STATES, _split  # GeoNames' US admin1 codes are USPS state abbreviations

CITIES_URL = "https://download.geonames.org/export/dump/cities1000.zip"
CACHE_FILE = DATA_DIR / "us_cities.tsv"
MAX_AGE_S = 30 * 24 * 3600


@dataclass
class City:
    name: str
    state: str
    lat: float
    lon: float
    population: int
    timezone: str | None

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


_cities: list[City] | None = None
_by_key: dict[str, City] = {}
_names: list[str] = []  # unique lower-case names, for fuzzy matching
_lock = asyncio.Lock()


def parse_geonames(text: str) -> list[City]:
    """Parse GeoNames rows (tab-separated) and keep one entry per "Name, ST"."""
    best: dict[str, City] = {}
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 18 or f[8] != "US" or f[10] not in US_STATES:
            continue
        try:
            c = City(name=f[1], state=f[10], lat=float(f[4]), lon=float(f[5]),
                     population=int(f[14] or 0), timezone=f[17] or None)
        except ValueError:
            continue
        key = c.label.lower()
        if key not in best or c.population > best[key].population:
            best[key] = c
    return sorted(best.values(), key=lambda c: -c.population)


async def _load_text() -> str:
    if CACHE_FILE.exists() and time.time() - CACHE_FILE.stat().st_mtime < MAX_AGE_S:
        return CACHE_FILE.read_text(encoding="utf-8")
    resp = await http.client().get(CITIES_URL, timeout=90)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        raw = z.read("cities1000.txt").decode("utf-8")
    us = "\n".join(line for line in raw.splitlines() if line.split("\t", 9)[8:9] == ["US"])
    CACHE_FILE.write_text(us, encoding="utf-8")
    return us


async def load() -> list[City]:
    global _cities, _names
    if _cities is None:
        async with _lock:
            if _cities is None:
                cities = parse_geonames(await _load_text())
                _by_key.update({c.label.lower(): c for c in cities})
                _names = sorted({c.name.lower() for c in cities})
                _cities = cities
    return _cities


async def lookup(name: str, state: str) -> City | None:
    """Exact "Name, ST" match, so a picked suggestion resolves to exactly that place."""
    await load()
    return _by_key.get(f"{name}, {state}".lower())


async def nearest_timezone(lat: float, lon: float) -> str | None:
    """Time zone of the closest listed city (airports in OurAirports carry no time zone)."""
    cities = await load()
    k = math.cos(math.radians(lat)) ** 2
    best = min(cities, key=lambda c: (c.lat - lat) ** 2 + k * (c.lon - lon) ** 2, default=None)
    return best.timezone if best else None


async def nearest_town(lat: float, lon: float, min_pop: int = 10_000, max_miles: float = 40) -> str | None:
    """'Texarkana, AR': the closest town big enough to be recognised, within max_miles; else the closest place."""
    cities = await load()
    k = math.cos(math.radians(lat)) ** 2
    d2 = lambda c: (c.lat - lat) ** 2 + k * (c.lon - lon) ** 2  # noqa: E731
    known = [c for c in cities if c.population >= min_pop]
    best = min(known, key=d2, default=None)
    if best is None or math.sqrt(d2(best)) * 69 > max_miles:  # degrees -> miles
        best = min(cities, key=d2, default=None)
    return best.label if best else None


def _close_names(q: str, n: int = 6) -> list[str]:
    """Names that look like a misspelling of q, also when q is only the start of a name."""
    scored = []
    for name in _names:
        if abs(len(name) - len(q)) > 3 and len(name) < len(q):
            continue
        head = name[: len(q)]
        sm = difflib.SequenceMatcher(None, q, head)
        if sm.real_quick_ratio() < 0.75 or sm.quick_ratio() < 0.75:
            continue
        r = max(sm.ratio(), difflib.SequenceMatcher(None, q, name).ratio())
        if r >= 0.75:
            scored.append((r, name))
    scored.sort(reverse=True)
    return [name for _, name in scored[:n]]


def _city_out(c: City) -> dict:
    pop = f"{c.population:,} people" if c.population else ""
    return {"value": c.label, "label": c.label, "detail": " · ".join(p for p in (US_STATES[c.state], pop) if p),
            "kind": "city", "timezone": c.timezone}


async def suggest(query: str, limit: int = 8) -> list[dict]:
    q_city, state = _split(query) if query.strip() else ("", None)
    q = q_city.strip().lower()
    if len(q) < 2:
        return []
    cities = await load()
    pool = [c for c in cities if c.state == state] if state else cities

    # 1. Names that start with what was typed, then names with a later word that does ("york" -> New York).
    starts = [c for c in pool if c.name.lower().startswith(q)]
    words = [c for c in pool if not c.name.lower().startswith(q)
             and any(w.startswith(q) for w in c.name.lower().split()[1:])]
    cities_out = (starts + words)[:limit]

    # 2. Close spellings when the prefix search finds (almost) nothing, i.e. a likely typo.
    if len(cities_out) < 2 and len(q) >= 3:
        close = set(_close_names(q))
        seen = {c.label for c in cities_out}
        cities_out += [c for c in pool if c.name.lower() in close and c.label not in seen][: limit - len(cities_out)]

    # 3. Airports by code ("den" -> DEN) or by city.
    out: list[dict] = []
    if not state:
        ap_rows = await airports.load()
        code_hits = [r for r in ap_rows if len(q) <= 3 and r.iata.lower().startswith(q)]
        code_hits.sort(key=lambda r: (r.iata.lower() != q, r.type != "large_airport", r.iata))
        out += [{"value": r.iata, "label": f"{r.iata} · {r.name}",
                 "detail": ", ".join(p for p in (r.city, r.state) if p), "kind": "airport",
                 "timezone": await nearest_timezone(r.lat, r.lon)} for r in code_hits[:3]]

    out = [o for o in out if o["kind"] == "airport" and o["value"].lower() == q] + \
          [_city_out(c) for c in cities_out] + \
          [o for o in out if not (o["kind"] == "airport" and o["value"].lower() == q)]
    return out[:limit]
