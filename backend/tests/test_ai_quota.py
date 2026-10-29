"""Gemini quota handling: a 429 skips the model (and its alias) until Google says it's back, without waiting."""
import asyncio
import time

import httpx
import pytest

from app import ai

DAILY = {"error": {"code": 429, "message": "You exceeded your current quota", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
         "quotaDimensions": {"model": "gemini-3.8-flash", "location": "global"}}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "9688s"}]}}
PER_MINUTE = {"error": {"code": 429, "message": "Rate limited", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "20s"}]}}
OK = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}


class FakeGemini:
    """Answers per model: the given body and status; records which models were called."""

    def __init__(self, answers):
        self.answers, self.called = answers, []

    async def post(self, url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        self.called.append(model)
        status, body = self.answers[model]
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(ai, "_cooldown", {})
    monkeypatch.setattr(ai, "_alias", {})
    monkeypatch.setattr(ai, "_no_thinking_cfg", set())
    monkeypatch.setattr(ai, "GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(ai, "GEMINI_FALLBACK_MODELS", ["gemini-flash-latest", "gemini-flash-lite-latest"])

    async def no_sleep(_):
        raise AssertionError("a 429 must not wait")

    monkeypatch.setattr(ai.asyncio, "sleep", no_sleep)

    def install(answers):
        fake = FakeGemini(answers)
        monkeypatch.setattr(ai.http, "client", lambda: fake)
        return fake
    return install


def test_reads_the_daily_quota_and_its_model():
    resp = httpx.Response(429, json=DAILY)
    assert ai._quota_wait(resp) == (9688, "gemini-3.8-flash", True)
    assert ai._quota_wait(httpx.Response(429, json=PER_MINUTE)) == (20, None, False)
    assert ai._quota_wait(httpx.Response(429, text="busy"))[0] == 60  # no details: a short skip


def test_daily_quota_skips_the_model_and_its_alias_on_later_calls(gemini):
    fake = gemini({"gemini-3.8-flash": (429, DAILY), "gemini-flash-latest": (429, DAILY),
                   "gemini-flash-lite-latest": (200, OK)})
    assert asyncio.run(ai._post({})) == OK
    assert fake.called == ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]  # no retries
    fake.called.clear()
    asyncio.run(ai._post({}))
    assert fake.called == ["gemini-flash-lite-latest"]  # both skipped: the alias shares the quota
    assert ai.last_model() == "gemini-flash-lite-latest"


def test_per_minute_limit_tries_the_better_model_again_once_its_back(gemini):
    fake = gemini({"gemini-3.8-flash": (429, PER_MINUTE), "gemini-flash-latest": (200, OK),
                   "gemini-flash-lite-latest": (200, OK)})
    asyncio.run(ai._post({}))
    assert fake.called == ["gemini-3.8-flash", "gemini-flash-latest"]
    ai._cooldown["gemini-3.8-flash"] = time.monotonic() - 1  # the 20 s have passed
    fake.answers["gemini-3.8-flash"] = (200, OK)
    fake.called.clear()
    asyncio.run(ai._post({}))
    assert fake.called == ["gemini-3.8-flash"]


def test_thinking_is_off_and_a_model_that_rejects_the_setting_gets_it_without(gemini, monkeypatch):
    sent = []

    class Picky(FakeGemini):  # the lite model: 400 "invalid argument" for any thinkingConfig
        async def post(self, url, json=None, headers=None, timeout=None):
            sent.append(json["generationConfig"].get("thinkingConfig"))
            has = "thinkingConfig" in json["generationConfig"]
            return httpx.Response(400 if has else 200, json={"error": {"message": "Request contains an invalid argument."}}
                                  if has else OK, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai, "GEMINI_FALLBACK_MODELS", [])
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "test")
    picky = Picky({})
    monkeypatch.setattr(ai.http, "client", lambda: picky)
    assert asyncio.run(ai._generate("sys", "prompt")) == {}
    assert sent == [ai.THINKING, None]  # asked for minimal thinking, then resent without it
    sent.clear()
    asyncio.run(ai._generate("sys", "prompt"))
    assert sent == [None]  # remembered: no failed request first


def test_all_models_used_up_fails_fast_with_when_they_return(gemini):
    fake = gemini({m: (429, DAILY) for m in ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]})
    with pytest.raises(ai.AIError):
        asyncio.run(ai._post({}))
    fake.called.clear()
    with pytest.raises(ai.AIError, match=r"back in 2\.\d h \(skipped\)"):
        asyncio.run(ai._post({}))
    assert fake.called == []  # no request at all: straight to the rule-based summary
