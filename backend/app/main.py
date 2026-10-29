"""FastAPI entrypoint. Run: uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import ai, http, perf, timing
from .assess import TripError, valid_tz, resolve_airports, assess, assess_stream
from .timeutil import tzinfo_for
from .config import CORS_ORIGINS, GEMINI_MODEL
from .models import AssessRequest, AssessResponse
from .sources import airports, flights, geocode, places


async def _warm() -> None:
    """Load the city and airport lists at startup so the first keystroke gets instant suggestions."""
    try:
        await asyncio.gather(places.load(), airports.load())
    except Exception:  # retried on first use
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    warm = asyncio.create_task(_warm())
    yield
    warm.cancel()
    await http.close()


app = FastAPI(title="Travel Disruption Checker", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

TRACED = {"/api/assess", "/api/flights", "/api/flight"}  # not /api/places: it runs on every keystroke


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    """Times each API call a check makes and prints the table to this console (see perf.py)."""
    if request.url.path not in TRACED:
        return await call_next(request)
    t = perf.begin(f"{request.method} {request.url.path}")
    resp = await call_next(request)
    resp.headers["Server-Timing"] = f"total;dur={t.ms()}"
    perf.log(t, resp.status_code)
    return resp


@app.get("/api/health")
async def health():
    return {"ok": True, "ai_enabled": ai.enabled(), "model": GEMINI_MODEL if ai.enabled() else None,
            "flight_lookup": flights.enabled()}


@app.get("/api/flights")
async def find_flights(origin: str = Query(..., min_length=2), destination: str = Query(..., min_length=2),
                       date: dt.date = Query(...), time: str = Query(..., pattern=r"^\d{2}:\d{2}$"),
                       from_home: bool = True, display_tz: str | None = Query(None, max_length=64)):
    """Scheduled flights from origin to destination that the traveller can catch (origin's local time).

    from_home: date+time is when they leave the origin city, so each airport's search starts after
    the drive there plus CHECKIN_H. Otherwise (a connection: origin is the connection airport) the
    search starts at date+time itself.
    """
    try:
        o, d = await asyncio.gather(geocode.resolve(origin), geocode.resolve(destination))
        o_aps, d_aps = await asyncio.gather(resolve_airports(o, origin), resolve_airports(d, destination))
    except geocode.PlaceNotFound as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    leave = dt.datetime.combine(date, dt.time.fromisoformat(time))
    view = valid_tz(display_tz)
    if from_home and view:  # the leave time is on the viewer's clock: put it on the origin's
        leave = leave.replace(tzinfo=ZoneInfo(view)).astimezone(tzinfo_for(o.timezone, o.lon)).replace(tzinfo=None)
    starts = {}
    for a in o_aps[:2]:  # at most 2 origin airports: each one costs a request from the free monthly quota
        lead = timing.CHECKIN_H + timing.ground_hours(airports.haversine_miles(o.lat, o.lon, a.lat, a.lon)) if from_home else 0
        starts[a.iata] = leave + dt.timedelta(minutes=round(lead * 60))
    try:
        found = await flights.search(starts, [a.iata for a in d_aps])
    except flights.LookupUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"results": found, "origin_airports": list(starts), "destination_airports": [a.iata for a in d_aps],
            "earliest": {k: v.strftime("%Y-%m-%dT%H:%M") for k, v in starts.items()}, "timings": perf.report()}


@app.get("/api/flight")
async def find_flight(number: str = Query(..., min_length=3, max_length=12), date: dt.date = Query(...)):
    """What a flight number flies on a date (local departure date): its legs, to fill in the form."""
    norm = flights.normalize_number(number)
    if not norm:
        raise HTTPException(status_code=400, detail="That doesn't look like a flight number. Use one like WN 3872.")
    try:
        legs = await flights.by_number(norm, date)
    except flights.LookupUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"number": norm, "legs": legs, "timings": perf.report()}


@app.get("/api/places")
async def suggest_places(q: str = Query("", max_length=80)):
    try:
        return {"results": await places.suggest(q)}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Place suggestions unavailable ({e.__class__.__name__}).") from e


@app.post("/api/assess", response_model=AssessResponse)
async def assess_trip(req: AssessRequest) -> AssessResponse:
    try:
        return await assess(req)
    except TripError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/assess/stream")
async def assess_trip_stream(req: AssessRequest):
    """The same assessment as progressive results, one JSON object per line (NDJSON).

    The first line comes as soon as the data is in (rules-only summary, ai_pending: true); the AI's
    version follows on the next line. A trip error before the first line is a normal 400.
    """
    t = perf.begin("POST /api/assess/stream")
    answers = assess_stream(req)
    try:
        first = await answers.__anext__()
    except TripError as e:
        perf.log(t, 400)
        raise HTTPException(status_code=400, detail=str(e)) from e
    print(f"  first answer sent after {t.ms()} ms", flush=True)

    async def lines():
        yield first.model_dump_json() + "\n"
        try:
            async for res in answers:
                yield res.model_dump_json() + "\n"
        except Exception as e:  # the first answer stands; say why the AI's didn't come
            yield json.dumps({"error": f"{e.__class__.__name__}: {str(e)[:200]}"}) + "\n"
        perf.log(t, 200)

    # no-cache / no buffering: proxies must pass each line on as soon as it's written
    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
