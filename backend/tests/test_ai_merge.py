"""News relevance and the assessment in one Gemini call, with the rating kept in step with the text."""
import asyncio
from datetime import date, timedelta

import pytest

from app import ai
from app import assess as assess_mod
from app.models import AssessRequest, Assessment, RiskItem
from tests.test_pipeline import stub  # noqa: F401  (autouse fixture: network stubbed)

ARTICLES = [
    {"title": "Celebrity wedding in Paris", "domain": "example.com", "seendate": "20261027T120000Z", "url": "https://e/1"},
    {"title": "JFK air traffic controller shortage causes ground stop", "domain": "example.org",
     "seendate": "20261027T130000Z", "url": "https://e/2"},
]


def text(headline, ids=()):
    return Assessment(headline=headline, summary="s", uncertainty="u", generated_by="gemini", actions=[],
                      risks=[RiskItem(title="r", severity="moderate", explanation="e", evidence_ids=list(ids))] if ids else [])


@pytest.fixture
def trip(monkeypatch):
    calls = []

    async def fake_news(tr, *args):
        return ARTICLES, "Google News"

    monkeypatch.setattr(assess_mod, "_news", fake_news)
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "test")

    async def synth(context, signals):
        calls.append(("synthesize", context["risk_level"]))
        return text("rewritten")

    monkeypatch.setattr(ai, "synthesize", synth)

    def run(picked):
        async def both(context, signals, trip, articles):
            calls.append(("both", context["risk_level"]))
            return picked, text("one call", [f"news-{p['index']}" for p in picked])

        monkeypatch.setattr(ai, "assess_with_news", both)
        req = AssessRequest(origin="New York, NY", destination="San Francisco, CA",
                            date=date.today() + timedelta(days=1), time="08:00", mode="fly")
        return asyncio.run(assess_mod.assess(req)), calls
    return run


def test_one_call_when_the_news_doesnt_change_the_rating(trip):
    res, calls = trip([])  # nothing relevant
    assert calls == [("both", res.risk_level)]
    assert res.assessment.headline == "one call" and not [s for s in res.signals if s.category == "news"]


def test_picked_news_becomes_evidence_the_assessment_can_cite(trip, monkeypatch):
    # the rating here depends only on news (the stub's weather already rates this trip high)
    monkeypatch.setattr(assess_mod.scoring, "score",
                        lambda sigs: (3, "medium") if any(s.category == "news" for s in sigs) else (0, "low"))
    res, calls = trip([{"index": 1, "severity": 2, "reason": "Ground stop at JFK"}])
    news = [s for s in res.signals if s.category == "news"]
    assert [s.id for s in news] == ["news-1"] and "AI-classified" in news[0].detail
    assert res.risk_level == "medium"  # the headline raised the rule rating...
    assert calls == [("both", "low"), ("synthesize", "medium")]  # ...so the text is rewritten for it
    assert res.assessment.headline == "rewritten"


def test_picked_news_that_keeps_the_rating_keeps_the_one_call_text(trip, monkeypatch):
    monkeypatch.setattr(assess_mod.scoring, "score", lambda sigs: (1, "low"))
    res, calls = trip([{"index": 1, "severity": 1, "reason": "Staffing issues at JFK"}])
    assert calls == [("both", "low")] and res.assessment.headline == "one call"
    assert res.assessment.risks[0].evidence_ids == ["news-1"]  # cites the headline it picked


def test_combined_call_failing_falls_back_to_keyword_news_and_the_summary_call(trip, monkeypatch):
    async def broken(*args):
        raise ai.AIError("bad JSON")

    res, calls = trip([])
    monkeypatch.setattr(ai, "assess_with_news", broken)
    calls.clear()
    req = AssessRequest(origin="New York, NY", destination="San Francisco, CA",
                        date=date.today() + timedelta(days=1), time="08:00", mode="fly")
    res = asyncio.run(assess_mod.assess(req))
    assert calls == [("synthesize", res.risk_level)] and res.assessment.headline == "rewritten"
    assert all("keyword-matched" in s.detail for s in res.signals if s.category == "news")


def test_parse_combined_answer_checks_citations():
    from app.models import Signal
    sig = Signal(id="om-o", source="Open-Meteo", category="weather", side="origin", location="JFK", severity=1,
                 title="Rain", detail="d")
    data = {"news": [{"index": "news-1", "severity": 5, "reason": "ground stop"}, {"index": 9}],
            "headline": "h", "summary": "s", "uncertainty": "u", "actions": [],
            "risks": [{"title": "a", "severity": "moderate", "explanation": "e", "evidence_ids": ["news-1", "om-o"]},
                      {"title": "b", "severity": "low", "explanation": "e", "evidence_ids": ["news-0"]}]}

    async def fake_generate(system, prompt, **kw):
        assert "news-1" in prompt and "JOB 1" in system
        return data

    import app.ai as mod
    orig = mod._generate
    mod._generate = fake_generate
    try:
        picked, a = asyncio.run(ai.assess_with_news({}, [sig], "trip", ARTICLES))
    finally:
        mod._generate = orig
    assert picked == [{"index": 1, "severity": 2, "reason": "ground stop"}]  # capped at moderate; 9 is out of range
    assert [r.title for r in a.risks] == ["a"]  # "b" cites a headline it didn't pick: dropped
