"""Airport lookup using the free OurAirports dataset (public domain).

The CSV (~12 MB) is downloaded once and cached under backend/data/ for 30 days.
We keep only US airports with scheduled passenger service and an IATA code.
"""
from __future__ import annotations

import csv
import io
import math
import time
from dataclasses import dataclass

from .. import http
from ..config import DATA_DIR
from ..models import Airport

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
CACHE_FILE = DATA_DIR / "airports.csv"
MAX_AGE_S = 30 * 24 * 3600


@dataclass
class _Row:
    iata: str
    icao: str | None
    name: str
    city: str | None
    state: str | None
    lat: float
    lon: float
    type: str


_rows: list[_Row] | None = None


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _load_text() -> str:
    if CACHE_FILE.exists() and time.time() - CACHE_FILE.stat().st_mtime < MAX_AGE_S:
        return CACHE_FILE.read_text(encoding="utf-8")
    text = await http.get(AIRPORTS_URL, as_json=False, ttl=MAX_AGE_S)
    CACHE_FILE.write_text(text, encoding="utf-8")
    return text


def parse_csv(text: str) -> list[_Row]:
    rows: list[_Row] = []
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("iso_country") != "US":
            continue
        if r.get("type") not in ("large_airport", "medium_airport"):
            continue
        if r.get("scheduled_service") != "yes":
            continue
        iata = (r.get("iata_code") or "").strip()
        if len(iata) != 3:
            continue
        try:
            lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
        except (KeyError, ValueError):
            continue
        icao = (r.get("icao_code") or r.get("gps_code") or r.get("ident") or "").strip() or None
        region = r.get("iso_region") or ""
        rows.append(
            _Row(
                iata=iata.upper(),
                icao=icao,
                name=r.get("name") or iata,
                city=r.get("municipality") or None,
                state=region.split("-")[-1] if region.startswith("US-") else None,
                lat=lat,
                lon=lon,
                type=r["type"],
            )
        )
    return rows


async def load() -> list[_Row]:
    global _rows
    if _rows is None:
        _rows = parse_csv(await _load_text())
    return _rows


def _to_model(r: _Row, dist: float) -> Airport:
    return Airport(iata=r.iata, icao=r.icao, name=r.name, city=r.city, state=r.state,
                   lat=r.lat, lon=r.lon, type=r.type, distance_miles=round(dist, 1))


async def by_iata(code: str) -> Airport | None:
    code = code.strip().upper()
    for r in await load():
        if r.iata == code:
            return _to_model(r, 0.0)
    return None


async def nearest(lat: float, lon: float, *, max_airports: int = 3) -> list[Airport]:
    """Pick the commercial airports a traveller would realistically use.

    Prefer large hubs within 60 miles (e.g. NYC -> JFK, LGA, EWR). If there are none,
    fall back to the closest airports with scheduled service within 150 miles, keeping
    the nearest large hub as well so regional travellers see their likely connection point.
    """
    rows = await load()
    scored = sorted(((haversine_miles(lat, lon, r.lat, r.lon), r) for r in rows), key=lambda x: x[0])
    large = [(d, r) for d, r in scored if r.type == "large_airport" and d <= 60]
    if large:
        return [_to_model(r, d) for d, r in large[:max_airports]]

    picked = [(d, r) for d, r in scored if d <= 150][:2]
    nearest_large = next(((d, r) for d, r in scored if r.type == "large_airport" and d <= 200), None)
    if nearest_large and all(nearest_large[1].iata != r.iata for _, r in picked):
        picked.append(nearest_large)
    if not picked and scored:
        picked = scored[:1]
    return [_to_model(r, d) for d, r in picked[:max_airports]]
