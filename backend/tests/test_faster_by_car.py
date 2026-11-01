"""Flying with no flight chosen: the page offers the car when it looks quicker door to door."""
import asyncio
from datetime import date, timedelta

from app import assess as assess_mod, timing
from app.models import AssessRequest
from tests.test_pipeline import stub  # noqa: F401  (autouse fixture: network stubbed)


def check(origin="New York, NY", destination="San Francisco, CA"):
    return asyncio.run(assess_mod.assess(AssessRequest(
        origin=origin, destination=destination, date=date.today() + timedelta(days=1), time="08:00", mode="fly")))


def test_a_short_hop_is_quicker_by_car_and_a_long_one_by_air():
    assert timing.drive_door_hours(35) < timing.air_door_hours(35, 6, 27)  # Dallas to Denton
    assert timing.drive_door_hours(2566) > timing.air_door_hours(2566, 10, 13)  # New York to San Francisco


def test_a_long_flight_gets_no_suggestion():
    assert check().faster_by_car is None


def test_when_driving_is_quicker_air_stays_the_choice_and_both_times_are_given(monkeypatch):
    monkeypatch.setattr(assess_mod.timing, "drive_door_hours", lambda dist: 0.75)
    monkeypatch.setattr(assess_mod.timing, "air_door_hours", lambda dist, to_mi, from_mi: 3.0)
    res = check()
    assert res.mode == "fly"
    assert res.faster_by_car.drive_time == "45 min" and res.faster_by_car.air_time == "3 h"
