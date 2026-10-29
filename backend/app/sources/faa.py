"""Live FAA National Airspace System status (nasstatus.faa.gov, free, no key).

Returns current ground stops, ground delay programs, general arrival/departure delays,
deicing and airport closures. These are *live* programs, so they are decisive only for
same-day travel; for later dates they are shown as context.

The XML schema has changed over the years, so the parser is deliberately generic: it
walks every <Delay_type>, and records each element that carries an <ARPT> child.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .. import http
from ..models import Airport, Signal

URL = "https://nasstatus.faa.gov/api/airport-status-information"
PAGE = "https://nasstatus.faa.gov/"


def _flatten(el: ET.Element, prefix: str = "", out: dict | None = None) -> dict:
    """Collect leaf text into {"Reason": ..., "Departure_Max": ...}; a Type="X" attribute prefixes children."""
    out = {} if out is None else out
    for child in el:
        p = prefix + (f"{child.attrib['Type']}_" if child.attrib.get("Type") else "")
        if len(child):
            _flatten(child, p, out)
        else:
            txt = (child.text or "").strip()
            if txt:
                out.setdefault(p + child.tag, txt)
    return out


def parse(xml_text: str) -> tuple[str | None, list[dict]]:
    root = ET.fromstring(xml_text)
    update = root.findtext("Update_Time")
    events: list[dict] = []
    for dt in root.iter("Delay_type"):
        kind = (dt.findtext("Name") or "Delay").strip()
        for el in dt.iter():
            arpt = el.find("ARPT")
            if arpt is None or not (arpt.text or "").strip():
                continue
            info = _flatten(el)
            info["kind"] = kind
            info["airport"] = arpt.text.strip().upper()
            events.append(info)
    return update, events


def _minutes(text: str | None) -> int:
    if not text:
        return 0
    t = text.lower()
    h = re.search(r"(\d+)\s*hour", t)
    m = re.search(r"(\d+)\s*min", t)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def classify(ev: dict) -> tuple[int, str]:
    kind = ev["kind"].lower()
    if "ground stop" in kind:
        return 3, "Ground stop in effect"
    if "closure" in kind:
        return 3, "Airport closure"
    if "ground delay" in kind:
        avg, mx = _minutes(ev.get("Avg")), _minutes(ev.get("Max"))
        return (3 if max(avg, mx) >= 120 else 2), "Ground delay program"
    if "deicing" in kind:
        return 1, "Deicing in progress"
    mins = max((_minutes(v) for k, v in ev.items() if k.endswith(("Max", "Min"))), default=0)
    return (2 if mins >= 45 else 1), "General arrival/departure delays"


async def fetch() -> str:
    return await http.get(URL, as_json=False, ttl=120, headers={"Accept": "application/xml"})


def relevance(days_out: int, hours_until: float | None) -> tuple[int, bool, str]:
    """(severity cap, counts toward the rating, note) for a live program, given how soon the trip is.

    Programs usually clear within hours, so with a departure time we can be precise:
    decisive within 6 hours, capped at moderate within 12, context after that.
    """
    if hours_until is not None:
        if hours_until <= 6:
            return 3, True, ""
        if hours_until <= 12:
            return 2, True, (f" (live status now; you depart in about {hours_until:.0f} hours and "
                             "programs often clear within a few hours)")
        return (1 if hours_until <= 36 else 0), False, (
            f" (live status now; you depart in about {hours_until:.0f} hours, so this may well have cleared)")
    if days_out == 0:
        return 3, True, ""
    note = " (this is the live status right now; it may not reflect your travel date)"
    return (1 if days_out == 1 else 0), False, note


def evaluate(xml_text: str, origin: list[Airport], dest: list[Airport], *, days_out: int,
             hours_until: float | None = None, via: list[Airport] | None = None) -> list[Signal]:
    """via: connection airports (side "route")."""
    update, events = parse(xml_text)
    via = via or []
    sides = {a.iata: "origin" for a in origin} | {a.iata: "route" for a in via} | {a.iata: "destination" for a in dest}
    cap, applies, note = relevance(days_out, hours_until)
    out: list[Signal] = []
    for ev in events:
        side = sides.get(ev["airport"])
        if not side:
            continue
        sev, label = classify(ev)
        sev = min(sev, cap)
        parts = [f"{k}: {v}" for k, v in ev.items() if k not in ("kind", "airport", "ARPT")]
        out.append(Signal(
            id=f"faa-{ev['airport']}-{len(out) + 1}", source="FAA NAS Status", category="airport_ops",
            side=side, location=ev["airport"], severity=sev, title=f"{label} at {ev['airport']}",
            detail=f"{ev['kind']}: " + "; ".join(parts)[:400] + note,
            observed_at=update, valid_for="live", url=PAGE, applies_to_travel_date=applies,
        ))
    for ap in origin + via + dest:
        if not any(s.location == ap.iata for s in out):
            out.append(Signal(
                id=f"faa-{ap.iata}-ok", source="FAA NAS Status", category="airport_ops",
                side=sides[ap.iata], location=ap.iata, severity=0,
                title=f"No FAA delay programs at {ap.iata} right now",
                detail="No ground stops, ground delay programs, closures or reported delays at the time of the check."
                       + ("" if applies and not note else " Live status only; check again close to departure."),
                observed_at=update, valid_for="live", url=PAGE, applies_to_travel_date=applies,
            ))
    return out
