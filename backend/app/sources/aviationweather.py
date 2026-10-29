"""Airport-specific aviation weather from aviationweather.gov (NOAA, free, no key).

- TAF: the official airport forecast (~24-30 h). It captures the things that actually cut
  airport capacity: low ceilings/visibility (IFR/LIFR), thunderstorms, strong gusts.
- METAR: current observed conditions; only decisive for same-day travel.
"""
from __future__ import annotations

import re
from datetime import datetime

from .. import http
from ..models import Airport, Signal
from ..timeutil import overlaps, parse_iso

API = "https://aviationweather.gov/api/data"
PAGE = "https://aviationweather.gov/data/taf/?ids="

CAT_ORDER = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


def parse_visibility(v) -> float | None:
    """Statute miles from values like 10, '6+', 'P6SM', '1/2', '1 1/2', '3SM'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).upper().replace("SM", "").replace("+", "").replace("P", "").strip()
    if not s:
        return None
    try:
        total = 0.0
        for part in s.split():
            if "/" in part:
                n, d = part.split("/", 1)
                total += float(n) / float(d)
            else:
                total += float(part)
        return total
    except (ValueError, ZeroDivisionError):
        return None


def ceiling_ft(clouds) -> float | None:
    bases = []
    for c in clouds or []:
        cover = (c.get("cover") or "").upper()
        if cover in ("BKN", "OVC", "OVX", "VV") and c.get("base") is not None:
            try:
                bases.append(float(c["base"]))
            except (TypeError, ValueError):
                pass
    return min(bases) if bases else None


def flight_category(vis: float | None, ceil: float | None) -> str:
    if (ceil is not None and ceil < 500) or (vis is not None and vis < 1):
        return "LIFR"
    if (ceil is not None and ceil < 1000) or (vis is not None and vis < 3):
        return "IFR"
    if (ceil is not None and ceil <= 3000) or (vis is not None and vis <= 5):
        return "MVFR"
    return "VFR"


def _wx_flags(wx: str | None) -> dict:
    w = (wx or "").upper()
    return {
        "ts": "TS" in w,
        "frz": "FZ" in w,
        "snow": bool(re.search(r"(^|\s|[+-])SN", w)) or "SHSN" in w,
        "heavy": "+" in w,
    }


async def fetch(kind: str, icaos: list[str]) -> list[dict]:
    if not icaos:
        return []
    data = await http.get(f"{API}/{kind}", params={"ids": ",".join(icaos), "format": "json"},
                          ttl=600, timeout=25, retries=1, retry_wait=2.0)
    return data if isinstance(data, list) else []


def evaluate_tafs(tafs: list[dict], airports: list[Airport], *, side: str,
                  window: tuple[datetime, datetime]) -> list[Signal]:
    by_icao = {a.icao: a for a in airports if a.icao}
    out: list[Signal] = []
    for taf in tafs:
        icao = taf.get("icaoId")
        ap = by_icao.get(icao)
        if not ap:
            continue
        valid_from = parse_iso(taf.get("validTimeFrom"))
        valid_to = parse_iso(taf.get("validTimeTo"))
        covers = overlaps(valid_from, valid_to, *window)
        worst_sev, reasons, worst_cat = 0, [], "VFR"
        for f in taf.get("fcsts") or []:
            t0, t1 = parse_iso(f.get("timeFrom")), parse_iso(f.get("timeTo"))
            if not overlaps(t0, t1, *window):
                continue
            vis, ceil = parse_visibility(f.get("visib")), ceiling_ft(f.get("clouds"))
            cat = flight_category(vis, ceil)
            flags = _wx_flags(f.get("wxString"))
            gust = f.get("wgst") or 0
            sev, why = 0, []
            if cat == "LIFR":
                sev, why = 2, why + [f"LIFR (vis {vis} mi, ceiling {ceil} ft)"]
            elif cat == "IFR":
                sev, why = 1, why + [f"IFR (vis {vis} mi, ceiling {ceil} ft)"]
            if flags["ts"]:
                sev, why = max(sev, 2), why + ["thunderstorms"]
            if flags["frz"]:
                sev, why = max(sev, 3), why + ["freezing precipitation"]
            if flags["snow"]:
                sev, why = max(sev, 2 if flags["heavy"] else 1), why + ["snow"]
            if isinstance(gust, (int, float)) and gust >= 25:
                sev, why = max(sev, 2 if gust >= 35 else 1), why + [f"gusts {gust} kt"]
            prob = f.get("probability")
            if prob and sev > 0:  # PROB30/40 groups are less certain
                sev -= 1
                why = [w + f" ({prob}% probability group)" for w in why]
            if CAT_ORDER.get(cat, 0) > CAT_ORDER.get(worst_cat, 0):
                worst_cat = cat
            if sev > worst_sev:
                worst_sev = sev
            reasons.extend(why)
        raw = (taf.get("rawTAF") or "").strip()
        if not covers:
            continue
        uniq = list(dict.fromkeys(reasons))
        title = (f"TAF: {', '.join(uniq[:3])}" if uniq else f"TAF: no significant restrictions ({worst_cat})")
        out.append(Signal(
            id=f"taf-{ap.iata}", source="AviationWeather.gov TAF", category="aviation_weather", side=side,
            location=ap.iata, severity=worst_sev, title=title,
            detail=f"Official terminal forecast for {ap.iata} during your travel day. Raw TAF: {raw[:400]}",
            observed_at=parse_iso(taf.get("issueTime")).isoformat() if parse_iso(taf.get("issueTime")) else None,
            valid_for=f"{valid_from.isoformat() if valid_from else '?'} → {valid_to.isoformat() if valid_to else '?'}",
            url=PAGE + (icao or ""),
        ))
    return out


def evaluate_metars(metars: list[dict], airports: list[Airport], *, side: str, same_day: bool) -> list[Signal]:
    by_icao = {a.icao: a for a in airports if a.icao}
    out: list[Signal] = []
    done: set[str] = set()
    for m in metars:
        ap = by_icao.get(m.get("icaoId"))
        if not ap or ap.iata in done:
            continue
        done.add(ap.iata)
        vis, ceil = parse_visibility(m.get("visib")), ceiling_ft(m.get("clouds"))
        cat = m.get("fltCat") or flight_category(vis, ceil)
        flags = _wx_flags(m.get("wxString"))
        sev = 0
        if same_day:
            sev = 2 if (cat == "LIFR" or flags["ts"] or flags["frz"]) else 1 if cat == "IFR" else 0
        obs = parse_iso(m.get("reportTime")) or parse_iso(m.get("obsTime"))
        out.append(Signal(
            id=f"metar-{ap.iata}", source="AviationWeather.gov METAR", category="aviation_weather", side=side,
            location=ap.iata, severity=sev,
            title=f"Now at {ap.iata}: {cat}" + (f", {m.get('wxString')}" if m.get("wxString") else ""),
            detail=f"Current observation (decisive only for same-day travel). Raw: {(m.get('rawOb') or '').strip()}",
            observed_at=obs.isoformat() if obs else None, valid_for="current conditions",
            url=PAGE.replace("taf", "metar") + (m.get("icaoId") or ""), applies_to_travel_date=same_day,
        ))
    return out
