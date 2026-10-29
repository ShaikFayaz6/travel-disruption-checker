"""Transparent, rule-based risk scoring.

Why rules and not the LLM for the headline rating? Ops teams need a rating they can audit
and that behaves the same for the same inputs. The AI layer explains and recommends; it
does not set the level.

- Only signals that apply to the travel date count.
- Per (source, side) we count only the worst signal, so three NYC airports reporting the same
  storm don't triple-count.
- Points: low = 1, moderate = 3, high = 6.
- Level: high if any high-severity signal or >= 8 points; medium if any moderate or >= 3 points.
"""
from __future__ import annotations

from .models import Signal, SourceStatus

POINTS = {0: 0, 1: 1, 2: 3, 3: 6}


def score(signals: list[Signal]) -> tuple[int, str]:
    worst: dict[tuple[str, str], int] = {}
    for s in signals:
        if not s.applies_to_travel_date or s.severity <= 0:
            continue
        k = (s.source, s.side)
        worst[k] = max(worst.get(k, 0), s.severity)
    pts = sum(POINTS[v] for v in worst.values())
    top = max(worst.values(), default=0)
    if top >= 3 or pts >= 8:
        return pts, "high"
    if top >= 2 or pts >= 3:
        return pts, "medium"
    return pts, "low"


def horizon_note(days_out: int, mode: str) -> str:
    if days_out == 0:
        return ("Travel is today: live FAA airport programs, current observations (METAR) and the "
                "official airport forecasts (TAF) are all in play, so this is the most reliable view.")
    if days_out == 1:
        return ("Travel is tomorrow: airport forecasts (TAF) and day-ahead weather forecasts are fairly "
                "reliable. Live FAA programs shown are today's and may clear; re-check on the day.")
    if days_out <= 7:
        return (f"Travel is {days_out} days out: based on multi-day weather forecasts and current news. "
                "Forecast details (timing, amounts) can shift; airport forecasts and live FAA status "
                "aren't available this far ahead.")
    if days_out <= 16:
        return (f"Travel is {days_out} days out: only extended forecasts are available, which are "
                "indicative at best. Treat this as an early look and re-check within a week of travel.")
    return (f"Travel is {days_out} days out, beyond the range of weather forecasts. Only systemic "
            "signals (news about strikes, outages, closures) can be assessed now.")


def confidence(days_out: int, sources: list[SourceStatus]) -> tuple[str, str]:
    ok = sum(1 for s in sources if s.status == "ok")
    failed = [s.name for s in sources if s.status == "error"]
    weather_failed = any("Open-Meteo" in n or "Weather Service" in n for n in failed)
    if days_out > 7:
        level = "low"
        reason = "the trip is more than a week away, beyond reliable forecast range"
    elif days_out >= 2:
        level = "medium" if ok >= 3 and not weather_failed else "low"
        reason = "multi-day forecasts are reasonably reliable for broad patterns but not exact timing"
    else:
        level = "high" if ok >= 4 and not weather_failed else "medium"
        reason = "near-term forecasts and live airport data are available"
    if failed:
        reason += f"; unavailable sources: {', '.join(failed)}"
    return level, reason[0].upper() + reason[1:] + "."
