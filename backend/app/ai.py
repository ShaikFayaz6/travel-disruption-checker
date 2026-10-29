"""Gemini integration (free tier via Google AI Studio key).

AI does two jobs, normally in one call (assess_with_news):
1. news relevance - decide which raw headlines actually bear on *this* trip and how much.
2. synthesis      - turn the evidence into a traveller-facing assessment: what the risks are,
                    why (citing signal ids), what to do, and how certain we are.
synthesize does job 2 alone: when there are no headlines, the combined call failed, or the news it
picked changed the rule rating (so the text is rewritten for the new level).

Guardrails: the model only sees the evidence we collected, must cite evidence ids, and any
risk citing ids that don't exist is dropped. The overall risk level comes from the
transparent rule-based score (scoring.py), not from the model, so the AI explains and
advises but cannot silently inflate or deflate the rating. If Gemini is unavailable, a
deterministic fallback produces the same structure so the app keeps working.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

import httpx
from pydantic import ValidationError

from . import http
from .config import GEMINI_API_KEY, GEMINI_FALLBACK_MODELS, GEMINI_MODEL
from .models import Action, Assessment, RiskItem, Signal

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class AIError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(GEMINI_API_KEY)


RETRYABLE = {500, 503, 504}  # busy: worth one retry. A 429 is a used-up quota: see _quota_wait
_last_model: str | None = None
# A model whose quota ran out is skipped until Google says it's back (RetryInfo.retryDelay): the whole
# day for the daily free-tier limit, seconds for the per-minute one. Retrying sooner only adds waits.
_cooldown: dict[str, float] = {}  # model -> time.monotonic() when it can be tried again
_alias: dict[str, str] = {}  # e.g. gemini-flash-latest -> gemini-3.8-flash: an alias shares its model's quota
# Thinking off: classifying headlines and writing JSON from given evidence don't need reasoning first,
# and a thinking model can spend seconds on it. "minimal" is the lowest level; models that reject the
# setting (some reject thinkingBudget: 0 with a 400, for one) get the request again without it.
THINKING = {"thinkingLevel": "minimal"}
_no_thinking_cfg: set[str] = set()  # models that rejected THINKING: sent without it from then on


def _for_model(body: dict, model: str) -> dict:
    if model not in _no_thinking_cfg:
        return body
    cfg = {k: v for k, v in body.get("generationConfig", {}).items() if k != "thinkingConfig"}
    return {**body, "generationConfig": cfg}


def last_model() -> str:
    return _last_model or GEMINI_MODEL


def _error_message(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", resp.text[:300])
    except ValueError:
        return resp.text[:300]


def _quota_wait(resp: httpx.Response) -> tuple[float, str | None, bool]:
    """From a 429: seconds until the quota is back, the model it belongs to, and whether it's the daily one."""
    try:
        details = resp.json().get("error", {}).get("details", [])
    except ValueError:
        details = []
    wait, quota_model, daily = None, None, False
    for d in details:
        kind = d.get("@type", "")
        if kind.endswith("RetryInfo"):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)s", d.get("retryDelay", ""))
            if m:
                wait = float(m.group(1))
        elif kind.endswith("QuotaFailure"):
            for v in d.get("violations", []):
                daily = daily or "PerDay" in v.get("quotaId", "")
                quota_model = quota_model or v.get("quotaDimensions", {}).get("model")
    if wait is None:  # no retryDelay given
        wait = 3600 if daily else 60
    return min(wait, 24 * 3600), quota_model, daily


def _cooling(model: str) -> float:
    """Seconds until this model (or the model it's an alias of) can be tried again; 0 = now."""
    until = max(_cooldown.get(model, 0), _cooldown.get(_alias.get(model, model), 0))
    return max(0.0, until - time.monotonic())


def _in(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min" if seconds >= 60 else f"{seconds:.0f} s"


async def _post(body: dict) -> dict:
    """Call Gemini: skip models whose quota is used up, retry busy ones once, fall back down the list."""
    global _last_model
    models = list(dict.fromkeys([GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]))
    errors = [f"{m}: quota used up, back in {_in(_cooling(m))} (skipped)" for m in models if _cooling(m)]
    for model in [m for m in models if not _cooling(m)]:
        for attempt in range(3):  # one retry when busy, one resend without the thinking setting
            try:
                resp = await http.client().post(
                    ENDPOINT.format(model=model), json=_for_model(body, model),
                    headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=60,
                )
            except httpx.HTTPError as e:
                errors.append(f"{model}: {e.__class__.__name__}")
                break
            if resp.status_code == 200:
                _last_model = model
                return resp.json()
            msg = _error_message(resp)
            if resp.status_code == 429:  # no waiting: on to the next model, and skip this one till it's back
                wait, quota_model, daily = _quota_wait(resp)
                _cooldown[model] = time.monotonic() + wait
                if quota_model and quota_model != model:
                    _alias[model] = quota_model
                    _cooldown[quota_model] = _cooldown[model]
                kind = "daily quota" if daily else "rate limit"
                print(f"  Gemini {model}: {kind} used up, skipped for {_in(wait)}", flush=True)
                errors.append(f"{model}: {kind} used up, back in {_in(wait)}")
                break
            if (resp.status_code == 400 and model not in _no_thinking_cfg
                    and "thinkingConfig" in body.get("generationConfig", {})):
                _no_thinking_cfg.add(model)  # doesn't take the thinking setting: same request without it
                print(f"  Gemini {model}: rejected the thinking setting, sending without it", flush=True)
                continue
            if resp.status_code in RETRYABLE and attempt == 0:
                await asyncio.sleep(3)
                continue
            errors.append(f"{model}: HTTP {resp.status_code} {msg[:120]}")
            if resp.status_code in (400, 401, 403) and "model" not in msg.lower():
                raise AIError(f"Gemini HTTP {resp.status_code}: {msg}")
            break
    raise AIError("All Gemini models failed — " + " | ".join(errors))


async def _generate(system: str, prompt: str, *, temperature: float = 0.2) -> dict:
    if not GEMINI_API_KEY:
        raise AIError("GEMINI_API_KEY is not set")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                             "thinkingConfig": THINKING},
    }
    data = await _post(body)
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise AIError(f"Unexpected Gemini response: {str(data)[:200]}") from e
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("Gemini did not return valid JSON") from e


# ---------------------------------------------------------------- news relevance
NEWS_SYSTEM = """You screen news headlines for a corporate travel-risk tool.
For each headline decide whether it plausibly affects THIS specific trip (its airports, cities,
the national US air-traffic system, or the airlines/route) on or around the travel date.
Be strict: generic, foreign, old, or unrelated stories are NOT relevant. A story about a past
event that is already resolved is not relevant unless effects continue.
severity: 0 = context only, 1 = low, 2 = moderate (could plausibly delay or cancel this trip).
Never use severity 3 for news alone. Return JSON only."""


def _headlines(articles: list[dict]) -> str:
    return "\n".join(f"[news-{i}] {a['title']} ({a.get('domain')}, seen {a.get('seendate')})"
                     for i, a in enumerate(articles))


def _parse_news(items, articles: list[dict]) -> list[dict]:
    """The model's relevant headlines: index into articles, severity capped at moderate, a reason."""
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        idx = it.get("index")
        if isinstance(idx, str):  # "news-3" or "3"
            idx = idx.removeprefix("news-")
        try:
            idx, sev = int(idx), int(it.get("severity", 1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(articles) and it.get("relevant", True) and idx not in {o["index"] for o in out}:
            out.append({"index": idx, "severity": max(0, min(2, sev)), "reason": str(it.get("reason", ""))[:240]})
    return out


# ---------------------------------------------------------------- synthesis
SYNTH_SYSTEM = """You are a travel-operations analyst writing a disruption assessment for an
employee about to travel within the United States.

Rules:
- Use ONLY the evidence provided. Do not add facts, events or numbers that are not in it.
- Every risk must cite the ids of the evidence items that support it (evidence_ids).
- Merge duplicate evidence (e.g. three nearby airports with the same storm) into one risk.
- Evidence with severity 0 or applies_to_travel_date=false is context; mention it only if useful.
- If a departure time is given, evidence is already filtered to the hours the traveller is at each
  place (travel_windows). Make timing concrete (e.g. "storms are forecast after your 7 AM departure
  has left"), and say the arrival time is an estimate for a nonstop flight or a straight drive.
  If no time is given, the whole day was checked; where timing matters, suggest which part of the
  day looks better if the evidence shows it.
- door_to_door (when given) has the drives to and from the airports and when to leave. If weather
  hits the drive to the airport (evidence at "Drive to ..."), suggest leaving earlier than
  leave_origin_by and say by how much; if it hits the drive from the airport, say so.
- better_departure (when given) is a nearby departure time the hourly forecast says avoids the
  worst weather. Make it one of the actions, with its time. Never suggest other times on your own.
- The overall risk level and confidence are already computed by rules; explain them, don't change them.
- Be honest about uncertainty: say how far out the trip is, which sources are forecasts vs live,
  and what could change. Note any data sources that failed.
- Actions must be concrete and proportionate (e.g. "No action needed; re-check the day before",
  "Book the morning departure", "Consider flying from LGA instead of EWR", "Check your airline's
  travel waiver page", "Leave 45 minutes earlier for the airport"). Do not invent airline
  policies or flight numbers.
- Plain, calm, specific language. No markdown.

- Be brief: the reader wants the answer, not a report. Don't repeat the route, date or times
  back to them (the page already shows them), and don't list sources that worked.

Return JSON:
{"headline": "<=12 words",
 "summary": "1-2 short sentences",
 "risks": [{"title": str, "severity": "low"|"moderate"|"high", "explanation": "1 sentence",
            "evidence_ids": [str, ...]}],
 "actions": [{"action": str, "why": "1 short sentence", "priority": "now"|"before_trip"|"day_of"|"optional"}],
 "uncertainty": "1 sentence: what could change and when to re-check"}
At most 3 actions. If there are no meaningful risks, return an empty risks list and say so in the summary."""


def _evidence_payload(signals: list[Signal]) -> list[dict]:
    """Each item in full, except background (severity 0, not for the travel date): its title says enough."""
    out = []
    for s in signals:
        item = {"id": s.id, "source": s.source, "side": s.side, "location": s.location,
                "severity": s.severity, "applies_to_travel_date": s.applies_to_travel_date, "title": s.title}
        if s.severity or s.applies_to_travel_date:
            item |= {"detail": s.detail[:500], "valid_for": s.valid_for, "observed_at": s.observed_at}
        out.append({k: v for k, v in item.items() if v is not None})
    return out


def _json(data) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


def _prompt(context: dict, signals: list[Signal]) -> str:
    # the AI only mentions sources that didn't answer, so the ones that did are left out
    context = {**context, "source_status": [s for s in context.get("source_status", []) if s.get("status") != "ok"]}
    return "Trip context:\n" + _json(context) + "\n\nEvidence:\n" + _json(_evidence_payload(signals))


async def synthesize(context: dict, signals: list[Signal]) -> Assessment:
    data = await _generate(SYNTH_SYSTEM, _prompt(context, signals))
    return _parse_assessment(data, {s.id for s in signals})


# ---------------------------------------------------------------- both in one call
COMBINED_SYSTEM = f"""Two jobs, one JSON answer.

JOB 1, news. {NEWS_SYSTEM.removesuffix(" Return JSON only.")}
The headlines are listed with ids like news-3. They are NOT in the evidence yet.

JOB 2, the assessment. Write it from the evidence plus the headlines you judged relevant in job 1
(cite those by their news-N id in evidence_ids). The risk_level and risk_score in the trip context
were computed by rules BEFORE news was added.

{SYNTH_SYSTEM.split("Return JSON:")[0].strip()}

Return JSON:
{{"news": [{{"index": int (the N of news-N), "severity": 0|1|2, "reason": "one short sentence"}}],
 {SYNTH_SYSTEM.split("Return JSON:")[1].strip().removeprefix("{")}
"news" lists only relevant headlines (it may be empty)."""


async def assess_with_news(context: dict, signals: list[Signal], trip: str,
                           articles: list[dict]) -> tuple[list[dict], Assessment]:
    """News relevance + synthesis in one Gemini call: (relevant headlines, assessment).

    The assessment may cite the headlines it picked (news-<index>, the ids assess.py gives them).
    """
    prompt = (_prompt({**context, "trip": trip}, signals) +
              f"\n\nHeadlines (job 1):\n{_headlines(articles)}")
    data = await _generate(COMBINED_SYSTEM, prompt)
    if not isinstance(data, dict):
        raise AIError("Gemini did not return a JSON object")
    picked = _parse_news(data.get("news"), articles)
    return picked, _parse_assessment(data, {s.id for s in signals} | {f"news-{p['index']}" for p in picked})


def _parse_assessment(data, valid_ids: set[str]) -> Assessment:
    if not isinstance(data, dict):
        raise AIError("Gemini did not return a JSON object")
    try:
        risks = []
        for r in data.get("risks") or []:
            ids = [i for i in r.get("evidence_ids") or [] if i in valid_ids]
            if not ids:  # hallucination guard: unsupported risks are dropped
                continue
            sev = r.get("severity") if r.get("severity") in ("low", "moderate", "high") else "moderate"
            risks.append(RiskItem(title=str(r["title"]), severity=sev,
                                  explanation=str(r.get("explanation", "")), evidence_ids=ids))
        actions = []
        for a in data.get("actions") or []:
            pr = a.get("priority") if a.get("priority") in ("now", "before_trip", "day_of", "optional") else "before_trip"
            actions.append(Action(action=str(a["action"]), why=str(a.get("why", "")), priority=pr))
        return Assessment(
            headline=str(data["headline"]), summary=str(data["summary"]), risks=risks, actions=actions,
            uncertainty=str(data.get("uncertainty", "")), generated_by="gemini", model=last_model(),
        )
    except (KeyError, TypeError, ValidationError) as e:
        raise AIError(f"Gemini output failed validation: {e.__class__.__name__}") from e


# ---------------------------------------------------------------- deterministic fallback
SEV_WORD = {1: "low", 2: "moderate", 3: "high"}


def fallback(context: dict, signals: list[Signal], error: str | None) -> Assessment:
    risky = sorted([s for s in signals if s.severity > 0], key=lambda s: -s.severity)
    groups: dict[tuple, list[Signal]] = {}
    for s in risky:
        groups.setdefault((s.side, s.category), []).append(s)
    risks = []
    for (side, cat), items in groups.items():
        top = items[0]
        risks.append(RiskItem(
            title=f"{top.title} ({side})", severity=SEV_WORD[top.severity],
            explanation=top.detail[:300], evidence_ids=[s.id for s in items][:6],
        ))
    level = context["risk_level"]
    actions = [Action(action="Re-check this assessment the day before and the morning of travel",
                      why="Forecasts and airport programs change; live FAA status only matters on the day.",
                      priority="day_of")]
    if level in ("medium", "high"):
        actions.insert(0, Action(action="Check your airline's app for travel waivers and enable flight alerts",
                                 why="Airlines often allow free changes when weather or operational issues are expected.",
                                 priority="before_trip"))
    if context.get("better_departure"):
        actions.insert(0, Action(action=context["better_departure"],
                                 why="Based on the hourly forecast at each place on your trip.", priority="before_trip"))
    elif level == "high":
        actions.insert(0, Action(action="Consider moving the trip earlier/later or choosing a different airport",
                                 why="High-severity signals are present on your route.", priority="now"))
    summary = (f"Rule-based assessment: {level} risk with {len(risky)} risk signal(s) found across "
               f"{len(groups)} area(s)." if risky else "No meaningful disruption signals were found for this trip.")
    return Assessment(
        headline=f"{level.capitalize()} disruption risk for {context['route']}",
        summary=summary, risks=risks, actions=actions,
        uncertainty=context["horizon_note"], generated_by="rules_fallback", ai_error=error,
    )
