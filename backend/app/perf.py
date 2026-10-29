"""Per-request timing: every outgoing API call and each pipeline step, printed to the console.

A trace is started per traced request (main.py middleware) and carried in a context variable, so
http.py can record calls without passing anything around. For each call:
  start_ms   when it started, from the start of the request (shows what ran in parallel)
  latency_ms time to the first byte of the response (network + the API's own processing)
  fetch_ms   the whole call, the response body included
Cache hits are listed too (0 ms), so it's clear which data wasn't fetched again.
"""
from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

HOSTS = {
    "api.open-meteo.com": "Open-Meteo forecast",
    "geocoding-api.open-meteo.com": "Open-Meteo geocoding",
    "api.weather.gov": "National Weather Service",
    "aviationweather.gov": "AviationWeather.gov",
    "nasstatus.faa.gov": "FAA NAS Status",
    "news.google.com": "Google News",
    "api.gdeltproject.org": "GDELT news",
    "generativelanguage.googleapis.com": "Gemini (AI)",
    "aerodatabox.p.rapidapi.com": "AeroDataBox",
}


@dataclass
class Trace:
    label: str
    t0: float = field(default_factory=time.perf_counter)
    calls: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)

    def ms(self, t: float | None = None) -> int:
        return round(((t or time.perf_counter()) - self.t0) * 1000)


_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar("trace", default=None)


def begin(label: str) -> Trace:
    t = Trace(label)
    _trace.set(t)
    return t


def _where(url: str) -> str:
    """The path, plus the point for per-location APIs (keys travel in headers, never in the URL)."""
    u = urlsplit(url)
    q = parse_qs(u.query)
    lat, lon = q.get("latitude", [None])[0], q.get("longitude", [None])[0]
    point = f" @{float(lat):.2f},{float(lon):.2f}" if lat and lon else ""
    return (u.path[:56] + point) or "/"


def call(url: str, start: float, first_byte: float | None, end: float, status: int | str,
         size: int | None = None, cached: str = "") -> None:
    t = _trace.get()
    if t is None:
        return
    host = urlsplit(url).netloc
    t.calls.append({
        "api": HOSTS.get(host, host), "where": _where(url), "start_ms": t.ms(start),
        "latency_ms": round((first_byte - start) * 1000) if first_byte else None,
        "fetch_ms": round((end - start) * 1000), "status": status,
        "kb": round(size / 1024, 1) if size is not None else None, "cache": cached,
    })


@contextmanager
def step(name: str):
    t, s = _trace.get(), time.perf_counter()
    try:
        yield
    finally:
        if t is not None:
            t.steps.append({"step": name, "start_ms": t.ms(s), "ms": round((time.perf_counter() - s) * 1000)})


def report(t: Trace | None = None) -> dict | None:
    t = t or _trace.get()
    if t is None:
        return None
    calls = sorted(t.calls, key=lambda c: c["start_ms"])
    fetched = [c for c in calls if not c["cache"]]
    per_api: dict[str, dict] = {}
    for c in calls:
        a = per_api.setdefault(c["api"], {"api": c["api"], "calls": 0, "cached": 0, "max_ms": 0, "sum_ms": 0})
        a["calls"] += 1
        a["cached"] += bool(c["cache"])
        a["max_ms"] = max(a["max_ms"], c["fetch_ms"])
        a["sum_ms"] += c["fetch_ms"]
    return {"total_ms": t.ms(), "fetched": len(fetched), "cached": len(calls) - len(fetched),
            "steps": t.steps, "per_api": sorted(per_api.values(), key=lambda a: -a["max_ms"]), "calls": calls}


def log(t: Trace, status: int) -> None:
    """A plain-ASCII table in the backend console (the Windows console mangles other characters)."""
    r = report(t)
    out = [f"\n=== {t.label}  HTTP {status}  total {r['total_ms']} ms  "
           f"({r['fetched']} API calls fetched, {r['cached']} from cache) ==="]
    if r["steps"]:
        out.append("  steps:")
        out += [f"    {s['start_ms']:>6} ms  +{s['ms']:>6} ms  {s['step']}" for s in r["steps"]]
    out.append(f"  calls:   {'start':>6}  {'latency':>8}  {'fetch':>7}  {'status':>6}  {'KB':>7}  api / where")
    for c in r["calls"]:
        lat = f"{c['latency_ms']} ms" if c["latency_ms"] is not None else "-"
        kb = f"{c['kb']}" if c["kb"] is not None else "-"
        out.append(f"    {c['start_ms']:>6} ms  {lat:>8}  {c['fetch_ms']:>4} ms  {str(c['status']):>6}  {kb:>7}  "
                   f"{c['api']} {c['where']}" + (f"  [{c['cache']}]" if c["cache"] else ""))
    if r["per_api"]:
        out.append("  slowest per API (max / sum of calls):")
        out += [f"    {a['max_ms']:>6} ms / {a['sum_ms']:>6} ms  {a['api']} ({a['calls']} calls, {a['cached']} cached)"
                for a in r["per_api"]]
    print("\n".join(out), flush=True)
