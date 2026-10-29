"""National Weather Service (api.weather.gov): official active alerts + text forecast.

Free, no key; NWS asks for an identifying User-Agent (set in config.USER_AGENT).
Alerts are the authoritative "is something official happening" signal. The text forecast
is used as corroborating context (severity 0) so it doesn't double-count Open-Meteo.
"""
from __future__ import annotations

from datetime import date, datetime

from .. import http
from ..models import Signal
from ..timeutil import overlaps, parse_iso

API = "https://api.weather.gov"
GEOJSON = {"Accept": "application/geo+json"}

# Travel impact by alert type (first match wins). NWS's own "severity" field is a fallback.
EVENT_IMPACT: list[tuple[str, int]] = [
    ("blizzard", 3), ("ice storm", 3), ("winter storm warning", 3), ("hurricane", 3),
    ("tornado warning", 3), ("tropical storm warning", 3), ("extreme wind", 3),
    ("winter storm watch", 2), ("winter weather advisory", 2), ("high wind", 2),
    ("dense fog", 2), ("freezing fog", 2), ("severe thunderstorm", 2), ("tornado watch", 2),
    ("flash flood", 2), ("flood warning", 2), ("lake effect snow", 2), ("snow squall", 2),
    ("tropical storm", 2), ("storm warning", 2), ("extreme cold", 2),
    ("rip current", 0), ("beach hazard", 0), ("small craft", 0), ("marine", 0), ("gale", 0),
    ("coastal flood statement", 0), ("hydrologic outlook", 0), ("high surf", 0), ("low water", 0),
    ("wind advisory", 1), ("freeze", 1), ("frost", 1), ("heat", 1), ("flood advisory", 1),
    ("special weather statement", 1), ("air quality", 1), ("dense smoke", 1),
]
NWS_SEVERITY = {"extreme": 3, "severe": 3, "moderate": 2, "minor": 1}


def alert_impact(event: str, nws_severity: str | None) -> int:
    e = (event or "").lower()
    for key, sev in EVENT_IMPACT:
        if key in e:
            return sev
    return min(2, NWS_SEVERITY.get((nws_severity or "").lower(), 1))


async def alerts(lat: float, lon: float) -> list[dict]:
    data = await http.get(f"{API}/alerts/active", params={"point": f"{lat:.4f},{lon:.4f}"},
                          headers=GEOJSON, ttl=300)
    return [f.get("properties") or {} for f in data.get("features") or []]


async def forecast_periods(lat: float, lon: float) -> list[dict]:
    meta = await http.get(f"{API}/points/{lat:.4f},{lon:.4f}", headers=GEOJSON, ttl=24 * 3600)
    url = (meta.get("properties") or {}).get("forecast")
    if not url:
        return []
    data = await http.get(url, headers=GEOJSON, ttl=1800)
    return (data.get("properties") or {}).get("periods") or []


def evaluate_alerts(props: list[dict], *, sid: str, side: str, label: str,
                    window: tuple[datetime, datetime]) -> list[Signal]:
    out: list[Signal] = []
    seen: set[str] = set()
    for p in props:
        event = p.get("event") or "Weather alert"
        start = parse_iso(p.get("onset")) or parse_iso(p.get("effective"))
        end = parse_iso(p.get("ends")) or parse_iso(p.get("expires"))
        applies = overlaps(start, end, *window)
        key = f"{event}|{p.get('ends') or p.get('expires')}"
        if key in seen:
            continue
        seen.add(key)
        sev = alert_impact(event, p.get("severity")) if applies else 0
        when = f"{start.isoformat() if start else '?'} → {end.isoformat() if end else '?'}"
        detail = (p.get("headline") or event).strip()
        if not applies:
            detail += " (active now, but not forecast to overlap your travel window)"
        instr = (p.get("instruction") or "").strip().replace("\n", " ")
        if instr and applies:
            detail += f" Guidance: {instr[:280]}"
        out.append(Signal(
            id=f"{sid}-{len(out) + 1}", source="National Weather Service alerts", category="weather",
            side=side, location=label, severity=sev, title=event, detail=detail,
            observed_at=p.get("sent"), valid_for=when, url=p.get("@id") or p.get("id") or "https://alerts.weather.gov/",
            applies_to_travel_date=applies,
        ))
    return out


def evaluate_forecast(periods: list[dict], *, sid: str, side: str, label: str, d: date,
                      lat: float, lon: float, window: tuple[datetime, datetime] | None = None) -> list[Signal]:
    """Forecast periods on the travel date, or (with a departure time) those overlapping the travel window."""
    out: list[Signal] = []
    page = f"https://forecast.weather.gov/MapClick.php?lat={lat:.4f}&lon={lon:.4f}"
    for p in periods:
        start = parse_iso(p.get("startTime"))
        if not start:
            continue
        if window:
            if not overlaps(start, parse_iso(p.get("endTime")), *window):
                continue
        elif start.date() != d:
            continue
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
        detail = (p.get("detailedForecast") or p.get("shortForecast") or "").strip()
        out.append(Signal(
            id=f"{sid}-{len(out) + 1}", source="National Weather Service forecast", category="weather",
            side=side, location=label, severity=0,
            title=f"{p.get('name', 'Forecast')}: {p.get('shortForecast', '')}".strip(),
            detail=detail + (f" (precipitation chance {pop}%)" if pop is not None else ""),
            valid_for=f"{p.get('startTime')} → {p.get('endTime')}",
            url=page,
        ))
    return out
