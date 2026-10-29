"""Departure-time windows, hourly weather inside them, and time-aware FAA relevance (offline)."""
from datetime import date, datetime, time, timedelta, timezone

from app import timing
from app.sources import faa, openmeteo

UTC = timezone.utc
CHI, LA = "America/Chicago", "America/Los_Angeles"


def test_no_time_means_whole_day():
    p = timing.build(date(2026, 11, 3), None, "fly", 1450, {"o": (0, CHI, -97), "d": (1, LA, -122)})
    assert not p.timed
    s, e = p.windows["o"]
    assert e - s == timedelta(days=1) and s == datetime(2026, 11, 3, 6, tzinfo=UTC)  # CST midnight


def test_fly_windows_follow_departure_and_estimated_arrival():
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    p = timing.build(date(2026, 11, 3), time(7, 30), "fly", 1450,
                     {"o": (0, CHI, -97), "d": (1, LA, -122)}, now=now)
    dep = datetime(2026, 11, 3, 13, 30, tzinfo=UTC)  # 7:30 CST
    assert p.departure == dep and round(p.hours_until, 1) == 25.5
    assert p.windows["o"] == (dep - timedelta(hours=3), dep + timedelta(hours=1))
    assert 3.5 < (p.arrival - dep).total_seconds() / 3600 < 4  # ~1450 mi nonstop
    assert p.windows["d"][0] == p.arrival - timedelta(hours=1)
    assert timing.clock(dep, CHI, -97) == "7:30 AM CST"
    assert timing.window_label(p.windows["o"], CHI, -97) == "Tue Nov 3, 4:30 AM–8:30 AM CST"


def test_long_drive_adds_overnight_stops():
    # ~1,370 straight-line miles -> ~26.5 h at the wheel -> 2 overnight stops
    p = timing.build(date(2026, 11, 3), time(16, 0), "drive", 1370,
                     {"o": (0, CHI, -97), "r4": (0.8, "America/New_York", -78), "d": (1, "America/New_York", -74)},
                     now=datetime(2026, 11, 1, tzinfo=UTC))
    assert p.stops == 2 and 26 < p.moving_hours < 27
    assert round((p.arrival - p.departure).total_seconds() / 3600, 1) == round(p.moving_hours + 28, 1)
    # 80% of the way = ~21 h of driving: after 2 overnight stops, so ~49 h after departure
    assert (p.windows["r4"][0] - p.departure).total_seconds() / 3600 > 45


def test_drive_route_points_are_timed_along_the_way():
    p = timing.build(date(2026, 11, 3), time(8, 0), "drive", 200,
                     {"o": (0, CHI, -97), "r1": (0.5, CHI, -97.5), "d": (1, CHI, -98)},
                     now=datetime(2026, 11, 3, 0, tzinfo=UTC))
    assert p.windows["o"][0] < p.windows["r1"][0] < p.windows["d"][0]


def hourly(day, storm_hours):
    """24 hourly slots (local, UTC-6) with thunderstorms and gusts only in storm_hours."""
    return {
        "utc_offset_seconds": -6 * 3600, "timezone": CHI,
        "daily": {"weather_code": [95], "precipitation_sum": [0.6], "precipitation_probability_max": [80],
                  "snowfall_sum": [0], "wind_gusts_10m_max": [45], "temperature_2m_max": [70],
                  "temperature_2m_min": [55]},
        "hourly_units": {"visibility": "m"},
        "hourly": {"time": [f"{day}T{h:02d}:00" for h in range(24)],
                   "weather_code": [95 if h in storm_hours else 1 for h in range(24)],
                   "wind_gusts_10m": [45 if h in storm_hours else 10 for h in range(24)],
                   "precipitation": [0.2 if h in storm_hours else 0 for h in range(24)],
                   "precipitation_probability": [80 if h in storm_hours else 5 for h in range(24)],
                   "snowfall": [0] * 24, "visibility": [20000] * 24},
    }


def test_door_to_door_adds_checkin_and_drives():
    dep, arr = datetime(2026, 11, 3, 20, 14, tzinfo=UTC), datetime(2026, 11, 3, 23, tzinfo=UTC)
    p = timing.from_legs([(dep, arr)], ["o", "d"], now=datetime(2026, 11, 1, tzinfo=UTC))
    assert timing.ground_hours(50) == 1.25  # 60 road miles at 50 mph, rounded up to 5 min
    assert timing.ground_hours(1) == 0.25  # never under 15 min...
    assert timing.ground_hours(0) == 0  # ...unless the place is the airport itself
    g = timing.ground(p, 1.25, 0.5)
    assert g.at_airport == dep - timedelta(hours=1)  # an hour for bag drop and security
    assert g.leave_home == dep - timedelta(hours=2.25)
    assert g.at_door == arr + timedelta(hours=1)  # 30 min for bags, then the drive
    assert g.to_window == (g.leave_home - timedelta(minutes=30), g.at_airport)


def test_better_departure_finds_the_nearest_clear_time():
    from app.assess import _better_departure
    local = timezone(timedelta(hours=-6))
    day = (datetime.now(local) + timedelta(days=2)).date()
    dep = datetime.combine(day, time(9), tzinfo=local).astimezone(UTC)
    p = timing.from_legs([(dep, dep + timedelta(hours=3))], ["o", "d"])
    data = hourly(day.isoformat(), storm_hours={5, 6, 7, 8, 9})  # storms until 10 AM at the origin
    better = _better_departure(p, None, {"o": ("ICT", data)}, {}, "fly")
    assert better is not None and better[0] == 4  # 1 PM: the origin window (10 AM-2 PM) misses the storms
    assert "at ICT" in better[1][0]
    calm = hourly(day.isoformat(), storm_hours=set())
    assert _better_departure(p, None, {"o": ("ICT", calm)}, {}, "fly") is None  # nothing to avoid


def test_evening_storm_does_not_count_for_morning_window():
    data = hourly("2026-11-03", storm_hours={19, 20, 21})
    morning = (datetime(2026, 11, 3, 10, tzinfo=UTC), datetime(2026, 11, 3, 15, tzinfo=UTC))  # 4-9 AM local
    sigs, summ = openmeteo.evaluate(data, sid="om-o", side="origin", label="DFW", window=morning, window_text="4–9 AM")
    assert [s.id for s in sigs] == ["om-o-ok"]  # the evening storm isn't shown at all
    assert summ["travel_window"]["gust"] == 10


def test_storm_inside_window_counts():
    data = hourly("2026-11-03", storm_hours={6, 7})
    morning = (datetime(2026, 11, 3, 10, tzinfo=UTC), datetime(2026, 11, 3, 15, tzinfo=UTC))
    sigs, _ = openmeteo.evaluate(data, sid="om-o", side="origin", label="DFW", window=morning, window_text="4–9 AM")
    assert any(s.id == "om-o-tstorm" and s.applies_to_travel_date for s in sigs)


def test_news_query_fits_gdelt_limit_and_keeps_main_airports():
    from app.models import Airport, Place
    from app.sources import news

    def ap(code, city):
        return Airport(iata=code, name=code, city=city, lat=0, lon=0, type="large_airport")

    p = Place(query="", name="x", lat=0, lon=0)
    origin = [ap("LAX", "Los Angeles"), ap("LGB", "Long Beach"), ap("BUR", "Burbank")]
    dest = [ap("DFW", "Dallas-Fort Worth"), ap("DAL", "Dallas")]
    q = news.build_query(p, p, origin, dest)
    assert len(q) <= news.MAX_QUERY_CHARS
    assert '"LAX airport"' in q and '"DFW airport"' in q and "-" not in q.split(")")[0]
    assert len(news.build_query(p, p, origin, dest, minimal=True)) < len(q)


def test_faa_relevance_by_hours_until_departure():
    assert faa.relevance(0, 2) == (3, True, "")
    assert faa.relevance(0, 10)[:2] == (2, True)
    assert faa.relevance(0, 20)[:2] == (1, False)  # later today: probably cleared by then
    assert faa.relevance(0, None)[:2] == (3, True)  # no time given: same-day counts fully
