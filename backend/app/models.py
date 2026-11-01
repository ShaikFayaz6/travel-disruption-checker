"""Pydantic models shared by the API and the assessment pipeline."""
from __future__ import annotations

from datetime import date, datetime
from datetime import time as dtime  # aliased: a field below is named `time`
from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = int  # 0 = info/context, 1 = low, 2 = moderate, 3 = high
RiskLevel = Literal["low", "medium", "high"]
Mode = Literal["auto", "fly", "drive"]


class FlightLeg(BaseModel):
    """One flight of the trip, with times as printed on the booking (local to each airport)."""

    flight: Optional[str] = Field(None, max_length=12, examples=["AA 1234"])
    dep_iata: str = Field(..., min_length=3, max_length=3, examples=["LAX"])
    # optional: the last flight defaults to the airport nearest the destination (a connection to the
    # next flight's departure airport), and a missing arrival is estimated from the distance
    arr_iata: Optional[str] = Field(None, min_length=3, max_length=3, examples=["DFW"])
    dep_local: datetime = Field(..., examples=["2026-11-02T09:00"])  # local at dep_iata
    arr_local: Optional[datetime] = Field(None, examples=["2026-11-02T14:05"])  # local at arr_iata


class AssessRequest(BaseModel):
    origin: str = Field(..., min_length=2, examples=["New York, NY"])
    destination: str = Field(..., min_length=2, examples=["San Francisco, CA"])
    date: date
    time: Optional[dtime] = Field(None, description="When the traveller sets off (HH:MM), local to the origin, "
                                                    "or to display_tz when that's given. Optional.",
                                 examples=["07:30"])
    mode: Mode = "auto"
    # the viewer's IANA time zone: every time in the response is shown in it, and date+time are read in it
    display_tz: Optional[str] = Field(None, max_length=64, examples=["America/Chicago"])
    # Optional: the actual flights. Their times replace the estimated arrival, and every
    # connection airport is checked too.
    legs: Optional[list[FlightLeg]] = Field(None, max_length=4)


class Segment(BaseModel):
    """One flight, for drawing the route: who flies it, how long, and the wait after it."""

    flight: Optional[str] = None  # None: an estimated flight (none chosen)
    dep_iata: str
    arr_iata: str
    minutes: int  # in the air, gate to gate
    estimated: bool = False  # the arrival (or the whole flight) is an estimate
    layover_min: Optional[int] = None  # at arr_iata before the next flight


class Timing(BaseModel):
    """How the departure time was laid out over the trip (only when a time was given)."""

    departure: str  # e.g. "7:30 AM CDT"
    est_arrival: str  # e.g. "9:12 AM PDT (+1 day)"
    duration_hours: float  # door to door, including overnight stops
    moving_hours: float  # in the air or at the wheel
    overnight_stops: int = 0
    hours_until_departure: float
    windows: dict[str, str]  # place label -> local window text
    basis: str  # the assumption behind the arrival estimate
    from_flights: bool = False  # times come from the traveller's flights, not an estimate
    legs: list[str] = []  # e.g. "AA 1234 · LAX 9:00 AM PST → DFW 2:05 PM CST"
    segments: list[Segment] = []  # flying: each flight in order (one estimated one if none chosen)
    # flying: door to door around the flights
    leave_by: Optional[str] = None  # leave the origin city, e.g. "12:05 PM CST"
    leave_local: Optional[str] = None  # the same as "YYYY-MM-DDTHH:MM" on the origin's clock, for the form
    at_airport_by: Optional[str] = None  # CHECKIN_H before take-off
    out_of_airport: Optional[str] = None  # after BAGS_H for deplaning and baggage claim
    door_arrival: Optional[str] = None  # at the destination city
    drive_to_airport: Optional[str] = None  # e.g. "Arkansas City, KS → ICT: about 1 h 15 min by car (60 mi)"
    drive_from_airport: Optional[str] = None
    drive_to_min: Optional[int] = None  # the same drives in minutes, for drawing the route
    drive_from_min: Optional[int] = None
    better_departure: Optional[str] = None  # a nearby departure time the hourly forecast says is clearer
    # flying without a chosen flight: the assumed nonstop flight
    est_flight: Optional[str] = None  # e.g. "BIH 7:55 AM PDT → TVC 3:07 PM EDT"
    cruise_mph: Optional[int] = None
    overhead_min: Optional[int] = None  # taxi, climb and approach
    flight_miles: Optional[int] = None  # airport to airport, straight line


class Place(BaseModel):
    query: str
    name: str
    state: Optional[str] = None
    lat: float
    lon: float
    timezone: Optional[str] = None


class Airport(BaseModel):
    iata: str
    icao: Optional[str] = None
    name: str
    city: Optional[str] = None
    state: Optional[str] = None
    lat: float
    lon: float
    type: str
    distance_miles: float = 0.0


class Signal(BaseModel):
    """One piece of evidence. Every risk shown to the user must point back to signals."""

    id: str
    source: str  # human-readable source name, e.g. "National Weather Service"
    category: Literal["weather", "airport_ops", "aviation_weather", "news", "road"]
    side: Literal["origin", "destination", "route", "national"]
    location: str  # e.g. "JFK" or "New York, NY"
    severity: Severity
    title: str
    detail: str
    observed_at: Optional[str] = None  # ISO timestamp of the data, when known
    valid_for: Optional[str] = None  # what time window this evidence describes
    url: Optional[str] = None
    applies_to_travel_date: bool = True


class SourceStatus(BaseModel):
    name: str
    status: Literal["ok", "error", "skipped", "no_data"]
    detail: str = ""
    latency_ms: Optional[int] = None
    url: Optional[str] = None


class RiskItem(BaseModel):
    title: str
    severity: Literal["low", "moderate", "high"]
    explanation: str
    evidence_ids: list[str]


class Action(BaseModel):
    action: str
    why: str
    priority: Literal["now", "before_trip", "day_of", "optional"]


class Assessment(BaseModel):
    headline: str
    summary: str
    risks: list[RiskItem]
    actions: list[Action]
    uncertainty: str
    generated_by: Literal["gemini", "rules_fallback"]
    model: Optional[str] = None
    ai_error: Optional[str] = None


class FasterByCar(BaseModel):
    drive_time: str  # e.g. "41 min"
    air_time: str  # door to door, e.g. "3 h"


class AssessResponse(BaseModel):
    request: AssessRequest
    origin: Place
    destination: Place
    mode: Literal["fly", "drive"]
    distance_miles: float
    days_out: int
    horizon_note: str
    timing: Optional[Timing] = None
    faster_by_car: Optional[FasterByCar] = None  # flying with no flight chosen, when driving looks quicker door to door
    origin_airports: list[Airport]
    destination_airports: list[Airport]
    risk_level: RiskLevel
    risk_score: int
    confidence: Literal["low", "medium", "high"]
    confidence_reason: str
    assessment: Assessment
    signals: list[Signal]
    sources: list[SourceStatus]
    generated_at: str
    timings: Optional[dict] = None  # perf.report(): each API call's latency and fetch time, and each step
    ai_pending: bool = False  # streamed first answer: rules only, the AI's summary and news filter follow
