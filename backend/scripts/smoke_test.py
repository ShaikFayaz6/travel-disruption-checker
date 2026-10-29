"""Live smoke test: hits every real data source once and prints what came back.

    cd backend && python -m scripts.smoke_test

Use this first if anything looks off — it shows which API is unreachable or changed shape.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta

from app import ai, http
from app.assess import assess
from app.models import AssessRequest
from app.sources import airports, aviationweather, faa, geocode, news, nws, openmeteo


async def check(name, coro):
    try:
        res = await coro
        print(f"  OK   {name}: {summarize(res)}")
        return res
    except Exception as e:
        print(f"  FAIL {name}: {e.__class__.__name__}: {e}")
        return None


def summarize(res) -> str:
    if isinstance(res, list):
        return f"{len(res)} items" + (f"; first: {json.dumps(res[0], default=str)[:160]}" if res else "")
    if isinstance(res, str):
        return f"{len(res)} chars: {res[:120]!r}"
    if hasattr(res, "model_dump"):
        return json.dumps(res.model_dump(), default=str)[:160]
    return json.dumps(res, default=str)[:160]


async def main() -> int:
    tomorrow = date.today() + timedelta(days=1)
    print("Data sources:")
    await check("Open-Meteo geocoding", geocode.resolve("Denton, TX"))
    aps = await check("OurAirports nearest to Denton", airports.nearest(33.21, -97.13))
    await check("Open-Meteo forecast (JFK)", openmeteo.fetch(40.64, -73.78, tomorrow))
    await check("NWS alerts (JFK)", nws.alerts(40.64, -73.78))
    await check("NWS forecast (JFK)", nws.forecast_periods(40.64, -73.78))
    await check("AviationWeather TAF", aviationweather.fetch("taf", ["KJFK", "KSFO"]))
    await check("AviationWeather METAR", aviationweather.fetch("metar", ["KJFK", "KSFO"]))
    xml = await check("FAA NAS status", faa.fetch())
    if xml:
        print("       parsed FAA events:", len(faa.parse(xml)[1]))
    await check("Google News", news.google_search(aps or [], [], ["Denton"]))
    q = '("JFK airport" OR "SFO airport" OR FAA) (delays OR cancellations OR outage) sourcelang:english'
    await check("GDELT news (backup)", news.fetch(q))

    print(f"\nGemini: {'enabled' if ai.enabled() else 'NOT configured (set GEMINI_API_KEY in backend/.env)'}")

    print("\nFull assessment New York, NY -> San Francisco, CA (tomorrow):")
    res = await assess(AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=tomorrow))
    print(f"  risk={res.risk_level} score={res.risk_score} confidence={res.confidence} "
          f"signals={len(res.signals)} by={res.assessment.generated_by}")
    print(f"  headline: {res.assessment.headline}")
    for s in res.sources:
        print(f"  [{s.status:7}] {s.name}: {s.detail}")
    await http.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
