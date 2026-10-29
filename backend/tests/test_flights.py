"""Flight legs (real times + connections) and the AeroDataBox departures parser (offline)."""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app import assess as assess_mod
from app import timing
from app.models import AssessRequest, FlightLeg
from app.sources import flights
from tests.test_pipeline import stub  # noqa: F401  (autouse fixture: network stubbed)

UTC = timezone.utc
CSV_EXTRA = "5,KCLT,large_airport,Charlotte Douglas Intl,35.214,-80.943,748,NA,US,US-NC,Charlotte,yes,KCLT,CLT,KCLT,,,,\n"


@pytest.fixture(autouse=True)
def with_clt(monkeypatch):
    import tests.test_pipeline as tp
    monkeypatch.setattr(tp, "CSV", tp.CSV + CSV_EXTRA)


def test_windows_from_legs_cover_the_layover():
    dep1, arr1 = datetime(2026, 11, 3, 14, tzinfo=UTC), datetime(2026, 11, 3, 17, tzinfo=UTC)
    dep2, arr2 = datetime(2026, 11, 3, 18, 30, tzinfo=UTC), datetime(2026, 11, 3, 20, tzinfo=UTC)
    p = timing.from_legs([(dep1, arr1), (dep2, arr2)], ["o", "c1", "d"], now=datetime(2026, 11, 1, tzinfo=UTC))
    assert p.windows["o"] == (dep1 - timedelta(hours=3), dep1 + timedelta(hours=1))
    assert p.windows["c1"] == (arr1 - timedelta(hours=1), dep2 + timedelta(hours=1))  # the whole layover
    assert p.windows["d"] == (arr2 - timedelta(hours=1), arr2 + timedelta(hours=2))
    assert p.arrival == arr2 and p.moving_hours == 4.5


def tomorrow_at(h, m=0):
    return datetime.combine(date.today() + timedelta(days=1), datetime.min.time()).replace(hour=h, minute=m)


def test_assessment_with_a_connection_checks_the_connection_airport():
    legs = [FlightLeg(flight="AA 100", dep_iata="JFK", arr_iata="CLT", dep_local=tomorrow_at(8), arr_local=tomorrow_at(10)),
            FlightLeg(flight="AA 200", dep_iata="CLT", arr_iata="SFO", dep_local=tomorrow_at(11), arr_local=tomorrow_at(13, 30))]
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=date.today(), legs=legs)
    res = asyncio.run(assess_mod.assess(req))
    assert res.mode == "fly" and res.request.date == date.today() + timedelta(days=1)  # date taken from the flight
    assert res.timing.from_flights and len(res.timing.legs) == 2
    assert "CLT (connection)" in res.timing.windows
    assert res.timing.leave_by and res.timing.at_airport_by and res.timing.door_arrival  # door to door
    s1, s2 = res.timing.segments  # for drawing the route: 2 h in the air, 1 h at CLT, 2.5 h
    assert (s1.flight, s1.minutes, s1.layover_min) == ("AA 100", 120, 60)
    assert (s2.dep_iata, s2.arr_iata, s2.minutes, s2.layover_min) == ("CLT", "SFO", 150 + 180, None)  # +3 h: ET -> PT
    assert any(s.location == "CLT (connection)" and s.side == "route" for s in res.signals)  # weather at CLT
    assert any(s.location == "CLT" and s.source == "FAA NAS Status" for s in res.signals)


def test_only_departure_needed_for_a_flight():
    # no arrival airport or time: lands at the airport nearest the destination, at an estimated time
    legs = [FlightLeg(flight="B6 15", dep_iata="JFK", dep_local=tomorrow_at(8))]
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=date.today(), legs=legs)
    res = asyncio.run(assess_mod.assess(req))
    assert res.destination_airports[0].iata == "SFO"
    assert "(arrival estimated)" in res.timing.legs[0] and "→ SFO about" in res.timing.legs[0]


def test_connection_entered_without_first_arrival():
    # the first flight's blanks come from the second: it lands where that one leaves
    legs = [FlightLeg(dep_iata="JFK", dep_local=tomorrow_at(8)),
            FlightLeg(dep_iata="CLT", arr_iata="SFO", dep_local=tomorrow_at(12), arr_local=tomorrow_at(14, 30))]
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=date.today(), legs=legs)
    res = asyncio.run(assess_mod.assess(req))
    assert "CLT (connection)" in res.timing.windows
    assert "→ CLT about" in res.timing.legs[0] and "estimated" not in res.timing.legs[1]


def test_times_shown_in_the_viewers_zone():
    # a New York flight seen from Los Angeles: every time is on Pacific time, the flight's own included
    legs = [FlightLeg(flight="AA 100", dep_iata="JFK", arr_iata="CLT", dep_local=tomorrow_at(8), arr_local=tomorrow_at(10))]
    req = AssessRequest(origin="New York, NY", destination="CLT", date=date.today(), legs=legs,
                        display_tz="America/Los_Angeles")
    res = asyncio.run(assess_mod.assess(req))
    t = res.timing
    assert t.departure.endswith((" PDT", " PST")) and t.legs[0].startswith(f"AA 100 · JFK {t.departure}")
    assert all(w.endswith((" PDT", " PST")) for w in t.windows.values())
    assert t.leave_by.endswith((" PDT", " PST")) and int(t.departure.split(":")[0]) < 8  # earlier on the west coast


def test_leave_time_read_in_the_viewers_zone():
    # leaving at 6:00 AM Chicago time is 7:00 AM in New York
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=date.today() + timedelta(days=1),
                        time="06:00", mode="drive", display_tz="America/Chicago")
    res = asyncio.run(assess_mod.assess(req))
    assert res.timing.departure.startswith("6:00 AM C") and res.request.time.hour == 7  # origin's clock inside


def test_stream_sends_the_rules_answer_before_the_ai_one(monkeypatch):
    from app import ai
    from app.models import Assessment

    async def fake_synth(context, signals):
        return Assessment(headline="AI headline", summary="s", risks=[], actions=[], uncertainty="u",
                          generated_by="gemini")

    monkeypatch.setattr(ai, "GEMINI_API_KEY", "test")
    async def fake_both(context, signals, trip, articles):
        return [], await fake_synth(context, signals)

    monkeypatch.setattr(ai, "synthesize", fake_synth)
    monkeypatch.setattr(ai, "assess_with_news", fake_both)
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=date.today() + timedelta(days=1),
                        time="08:00", mode="fly")

    async def collect():
        return [r async for r in assess_mod.assess_stream(req)]

    first, last = asyncio.run(collect())
    assert first.ai_pending and first.assessment.generated_by == "rules_fallback" and not first.assessment.ai_error
    assert not last.ai_pending and last.assessment.headline == "AI headline"
    assert first.timing == last.timing and first.risk_level == last.risk_level


def test_flying_on_past_the_destinations_airport_is_rejected():
    # to San Francisco: lands at SFO, then flies on to CLT (and would drive back): an extra flight
    legs = [FlightLeg(flight="UA 1", dep_iata="JFK", arr_iata="SFO", dep_local=tomorrow_at(8), arr_local=tomorrow_at(11)),
            FlightLeg(flight="UA 2", dep_iata="SFO", arr_iata="CLT", dep_local=tomorrow_at(12), arr_local=tomorrow_at(20))]
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=date.today(), legs=legs)
    with pytest.raises(assess_mod.TripError, match=r"UA 1 already lands at SFO.*UA 2 then takes you on to CLT"):
        asyncio.run(assess_mod.assess(req))


def test_legs_that_dont_connect_are_rejected():
    legs = [FlightLeg(dep_iata="JFK", arr_iata="CLT", dep_local=tomorrow_at(8), arr_local=tomorrow_at(10)),
            FlightLeg(dep_iata="DFW", arr_iata="SFO", dep_local=tomorrow_at(11), arr_local=tomorrow_at(13))]
    req = AssessRequest(origin="New York, NY", destination="SFO", date=date.today(), legs=legs)
    with pytest.raises(assess_mod.TripError, match="don't connect"):
        asyncio.run(assess_mod.assess(req))


def test_only_the_first_flight_of_a_connection_asks_for_the_next():
    legs = [FlightLeg(dep_iata="JFK", arr_iata="CLT", dep_local=tomorrow_at(8), arr_local=tomorrow_at(10))]
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA", date=date.today(), legs=legs)
    with pytest.raises(assess_mod.TripError, match="Add your next flight"):
        asyncio.run(assess_mod.assess(req))


def test_connection_departing_before_landing_is_rejected():
    legs = [FlightLeg(dep_iata="JFK", arr_iata="CLT", dep_local=tomorrow_at(8), arr_local=tomorrow_at(10)),
            FlightLeg(dep_iata="CLT", arr_iata="SFO", dep_local=tomorrow_at(9), arr_local=tomorrow_at(13))]
    req = AssessRequest(origin="New York, NY", destination="SFO", date=date.today(), legs=legs)
    with pytest.raises(assess_mod.TripError, match="before the previous flight lands"):
        asyncio.run(assess_mod.assess(req))


FIDS = {"departures": [
    {"number": "AA 2345", "status": "Expected", "airline": {"name": "American Airlines"},
     "departure": {"scheduledTime": {"utc": "2026-11-02 17:00Z", "local": "2026-11-02 09:00-08:00"}},
     "arrival": {"airport": {"iata": "DFW", "name": "Dallas"},
                 "scheduledTime": {"utc": "2026-11-02 20:05Z", "local": "2026-11-02 14:05-06:00"}}},
    {"number": "WN 10", "status": "Canceled", "airline": {"name": "Southwest"},
     "departure": {"scheduledTime": {"local": "2026-11-02 10:00-08:00"}},
     "arrival": {"airport": {"iata": "DAL"}, "scheduledTime": {"local": "2026-11-02 15:00-06:00"}}},
    {"number": "DL 77", "airline": {"name": "Delta"},  # older API shape
     "departure": {"scheduledTimeLocal": "2026-11-02 11:00-08:00"},
     "arrival": {"airport": {"iata": "ATL"}, "scheduledTimeLocal": "2026-11-02 18:40-05:00"}},
]}


def test_parse_departures_handles_both_shapes_and_skips_cancelled():
    out = flights.parse_departures(FIDS, "lax")
    assert [(f["flight"], f["arr_iata"], f["dep_local"], f["arr_local"]) for f in out] == [
        ("AA 2345", "DFW", "2026-11-02T09:00", "2026-11-02T14:05"),
        ("DL 77", "ATL", "2026-11-02T11:00", "2026-11-02T18:40"),
    ]


def test_search_keeps_only_flights_to_the_destination(monkeypatch):
    async def fake_departures(dep, start):
        return flights.parse_departures(FIDS, dep)

    monkeypatch.setattr(flights, "departures", fake_departures)
    out = asyncio.run(flights.search({"LAX": datetime(2026, 11, 2, 8)}, ["DFW", "DAL"]))
    assert [f["flight"] for f in out] == ["AA 2345"]


def test_flight_numbers_are_tidied():
    assert flights.normalize_number("wn3872") == "WN 3872"
    assert flights.normalize_number(" WN  3872 ") == "WN 3872"
    assert flights.normalize_number("b615") == "B6 15"
    assert flights.normalize_number("aal123") == "AAL 123"
    assert flights.normalize_number("3872") is None and flights.normalize_number("hello") is None


def test_parse_flight_by_number():
    data = [{"number": "WN 3872", "airline": {"name": "Southwest Airlines"},
             "departure": {"airport": {"iata": "SLC"}, "scheduledTime": {"local": "2026-10-29 06:00-06:00"}},
             "arrival": {"airport": {"iata": "DEN"}, "scheduledTime": {"local": "2026-10-29 07:25-06:00"}}}]
    assert flights.parse_flight(data) == [{"flight": "WN 3872", "airline": "Southwest Airlines", "dep_iata": "SLC",
                                           "arr_iata": "DEN", "dep_local": "2026-10-29T06:00",
                                           "arr_local": "2026-10-29T07:25"}]


def test_lookup_without_key_explains_how_to_continue(monkeypatch):
    monkeypatch.setattr(flights, "AERODATABOX_API_KEY", "")
    with pytest.raises(flights.LookupUnavailable, match="enter your flights by hand"):
        asyncio.run(flights.departures("LAX", datetime(2026, 11, 2, 8)))
