"""Day-specific weather forecast for a point from Open-Meteo (free, no key, up to 16 days).

This is our *quantitative* weather signal: snowfall, gusts, precipitation, thunderstorm
and freezing-rain weather codes, and low visibility (fog) during travel hours.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .. import http
from ..models import Signal

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PUBLIC_URL = "https://open-meteo.com/"

WMO = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
    81: "Heavy rain showers", 82: "Violent rain showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Severe thunderstorms with hail",
}
FREEZING = {56, 57, 66, 67}
THUNDER = {95, 96, 99}


async def fetch(lat: float, lon: float, d: date, end: date | None = None) -> dict:
    """Daily values for d (index 0) plus hourly values from d to end (for a departure-time window)."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,snowfall_sum,wind_gusts_10m_max",
        "hourly": "visibility,weather_code,precipitation,precipitation_probability,snowfall,wind_gusts_10m",
        "timezone": "auto",
        "start_date": d.isoformat(),
        "end_date": (end or d).isoformat(),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    return await http.get(FORECAST_URL, params=params, ttl=1800)


def _first(daily: dict, key: str):
    vals = daily.get(key) or []
    return vals[0] if vals else None


def _vis_factor(data: dict) -> float:
    unit = (data.get("hourly_units") or {}).get("visibility", "m")
    return 1 / 5280 if "ft" in unit else 1 / 1609.34


def _min_visibility_miles(data: dict, day: str | None = None) -> float | None:
    """Lowest daytime (6 AM-10 PM local) visibility on `day` (default: the first forecast day)."""
    hourly = data.get("hourly") or {}
    times, vis = hourly.get("time") or [], hourly.get("visibility") or []
    d0 = day or (times[0][:10] if times else "")
    daytime = [v for t, v in zip(times, vis) if v is not None and t[:10] == d0 and 6 <= int(t[11:13]) <= 22]
    return round(min(daytime) * _vis_factor(data), 2) if daytime else None


def _daily_stats(data: dict, i: int = 0) -> dict:
    """Daily numbers for forecast day i (0 = travel date)."""
    daily = data.get("daily") or {}

    def at(k: str):
        vals = daily.get(k) or []
        return vals[i] if i < len(vals) else None

    days = daily.get("time") or []
    return {"code": at("weather_code"), "snow": at("snowfall_sum"), "gust": at("wind_gusts_10m_max"),
            "precip": at("precipitation_sum"), "prob": at("precipitation_probability_max"),
            "vis": _min_visibility_miles(data, days[i] if i < len(days) else None)}


def _local_day_index(data: dict, when: datetime) -> int:
    """Which daily entry covers `when` at this point (a long drive reaches later points on later days)."""
    days = (data.get("daily") or {}).get("time") or []
    local = (when + timedelta(seconds=data.get("utc_offset_seconds") or 0)).date().isoformat()
    return days.index(local) if local in days else 0


def window_stats(data: dict, window: tuple[datetime, datetime]) -> dict | None:
    """The same numbers as the daily summary, but only over the hours inside `window` (UTC)."""
    h = data.get("hourly") or {}
    off = timedelta(seconds=data.get("utc_offset_seconds") or 0)
    idx = []
    for i, t in enumerate(h.get("time") or []):
        start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - off  # hourly times are local
        if start < window[1] and start + timedelta(hours=1) > window[0]:
            idx.append(i)
    if not idx:
        return None

    def col(k: str) -> list:
        vals = h.get(k) or []
        return [vals[i] for i in idx if i < len(vals) and vals[i] is not None]

    codes, vis = col("weather_code"), col("visibility")
    code = (next((c for c in codes if c in FREEZING), None) or next((c for c in codes if c in THUNDER), None)
            or (max(codes) if codes else None))
    return {"code": code, "snow": round(sum(col("snowfall")), 2), "gust": max(col("wind_gusts_10m"), default=None),
            "precip": round(sum(col("precipitation")), 2), "prob": max(col("precipitation_probability"), default=None),
            "vis": round(min(vis) * _vis_factor(data), 2) if vis else None}


def window_risk(data: dict, window: tuple[datetime, datetime], mode: str = "fly") -> tuple[int, list[str]] | None:
    """(worst severity, what causes it) inside the window, or None if the forecast doesn't cover it.
    The same thresholds as evaluate(), for comparing alternative departure times."""
    ws = window_stats(data, window)
    if ws is None:
        return None
    low_prob = ws["prob"] is not None and ws["prob"] < 30
    found = [(sev - (1 if pd and low_prob and sev > 0 else 0), title) for _, sev, title, _, pd in _findings(ws, "", "", mode)]
    worst = max((s for s, _ in found), default=0)
    return worst, [t for s, t in found if s == worst and s > 0]


# Why each kind of weather matters, for a flight vs a drive.
IMPACT = {
    "fly": {"ice": "deicing and ground delays are likely",
            "tstorm": "these commonly trigger ground stops and arrival restrictions",
            "wind": "strong crosswinds can reduce runway capacity",
            "rain": "heavy rain slows airport arrival rates",
            "vis": "fog and low cloud reduce airport arrival rates"},
    "drive": {"ice": "roads and bridges may be icy and dangerous",
              "tstorm": "heavy downpours, lightning and possible flash flooding make roads slow and hazardous",
              "wind": "strong crosswinds are hazardous, especially for high-profile vehicles",
              "rain": "expect slower traffic, spray and possible standing water",
              "vis": "fog slows highway traffic and raises the risk of crashes"},
}


def _findings(st: dict, label: str, when: str, mode: str = "fly") -> list[tuple[str, int, str, str, bool]]:
    """(key, severity, title, detail, precipitation-dependent) for each risky number in st."""
    code, snow, gust, precip, vis = st["code"], st["snow"], st["gust"], st["precip"], st["vis"]
    why = IMPACT["drive" if mode == "drive" else "fly"]
    out = []
    if snow and snow > 0:
        sev = 3 if snow >= 2 else 2 if snow >= 0.5 else 1
        out.append(("snow", sev, f"Snow forecast: {snow:.1f} in", f"Forecast snowfall of {snow:.1f} in at {label}{when}.", True))
    if code in FREEZING:
        out.append(("ice", 3, f"{WMO[code]} forecast", f"Freezing precipitation ({WMO[code].lower()}) is forecast at {label}{when}; {why['ice']}.", True))
    if code in THUNDER:
        out.append(("tstorm", 2, f"{WMO[code]} forecast", f"Thunderstorms are forecast at {label}{when}; {why['tstorm']}.", True))
    if gust is not None and gust >= 30:
        sev = 3 if gust >= 50 else 2 if gust >= 40 else 1
        out.append(("wind", sev, f"Wind gusts up to {gust:.0f} mph", f"Forecast peak gusts of {gust:.0f} mph at {label}{when}; {why['wind']}.", False))
    if precip is not None and precip >= 0.4 and code not in THUNDER and code not in FREEZING:
        sev = 2 if precip >= 1.0 else 1
        out.append(("rain", sev, f"Heavy precipitation: {precip:.2f} in", f"Forecast precipitation of {precip:.2f} in at {label}{when}; {why['rain']}.", True))
    if vis is not None and vis < 3:
        sev = 2 if vis < 1 else 1
        out.append(("vis", sev, f"Low visibility (down to {vis} mi)", f"Forecast visibility drops to {vis} mi at {label}{when}; {why['vis']}.", False))
    return out


def evaluate(data: dict, *, sid: str, side: str, label: str,
             window: tuple[datetime, datetime] | None = None, window_text: str | None = None,
             mode: str = "fly") -> tuple[list[Signal], dict]:
    """Turn a forecast into signals. Returns (signals, compact summary for the AI/UI).

    With a travel window (departure time given), only the hours inside it count; anything
    the daily forecast flags outside it is kept as context that doesn't affect the rating.
    """
    daily = data.get("daily") or {}
    code = _first(daily, "weather_code")
    summary = {
        "conditions": WMO.get(code, f"code {code}") if code is not None else None,
        "high_f": _first(daily, "temperature_2m_max"),
        "low_f": _first(daily, "temperature_2m_min"),
        "precip_in": _first(daily, "precipitation_sum"),
        "precip_prob_pct": _first(daily, "precipitation_probability_max"),
        "snow_in": _first(daily, "snowfall_sum"),
        "gust_mph": _first(daily, "wind_gusts_10m_max"),
        "min_daytime_visibility_mi": _min_visibility_miles(data),
        "timezone": data.get("timezone"),
    }
    signals: list[Signal] = []

    def add(key: str, sev: int, title: str, detail: str, precip_dependent: bool, prob, valid_for: str,
            applies: bool = True):
        # a low probability of precipitation tempers precipitation-driven risks
        if precip_dependent and prob is not None and prob < 30 and sev > 0:
            sev -= 1
            detail += f" (precipitation probability only {prob}%, so severity reduced)"
        signals.append(Signal(
            id=f"{sid}-{key}", source="Open-Meteo forecast", category="weather", side=side,
            location=label, severity=sev, title=title, detail=detail,
            valid_for=valid_for, url=PUBLIC_URL, applies_to_travel_date=applies,
        ))

    ws = window_stats(data, window) if window else None
    if window and ws is None:  # e.g. a long drive reaching this point beyond the 16-day forecast
        add("none", 0, "No forecast yet for when you pass here",
            f"The forecast doesn't reach {window_text} yet at {label}. Re-check closer to the trip.",
            False, None, window_text or "", applies=False)
        return signals, summary

    di = _local_day_index(data, window[0]) if window else 0
    day = _daily_stats(data, di)
    hi = next(iter((daily.get("temperature_2m_max") or [])[di:di + 1]), None)
    lo = next(iter((daily.get("temperature_2m_min") or [])[di:di + 1]), None)
    st, valid_for = (ws, f"{window_text} (hourly forecast)") if ws else (day, "travel date (daily forecast)")
    found = _findings(st, label, " during your travel window" if ws else "", mode)
    for key, sev, title, detail, pd in found:
        add(key, sev, title, detail, pd, st["prob"], valid_for)
    if ws:  # weather at other times of day is left out: only the hours you're there matter
        summary["travel_window"] = {"window": window_text, **ws,
                                    "conditions": WMO.get(ws["code"], None) if ws["code"] is not None else None}
    if not found:
        c = WMO.get(st["code"], f"code {st['code']}") if st["code"] is not None else summary["conditions"]
        where = f" during your travel window ({window_text})" if ws else ""
        add("ok", 0, f"No significant weather: {c}",
            f"{c}{where}; high {hi}°F / low {lo}°F for the day, "
            f"precip {st['precip']} in ({st['prob']}% chance), gusts {st['gust']} mph at {label}.",
            False, None, valid_for)
    return signals, summary
