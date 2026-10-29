"""Recent news about disruptions: Google News RSS search first, GDELT DOC 2.0 API as backup (both free, no key).

Weather and FAA feeds miss human/systemic causes: strikes, IT outages, ATC staffing,
security incidents, airport closures, government shutdowns. We pull recent English-language
headlines that mention the trip's airports/cities or national aviation systems together with
disruption keywords. Raw headlines are noisy, so Gemini classifies each for relevance
(see ai.assess_with_news); a keyword fallback is used when no API key is configured.
"""
from __future__ import annotations

import asyncio
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from .. import http
from ..models import Airport, Place

URL = "https://api.gdeltproject.org/api/v2/doc/doc"

DISRUPTION_TERMS = ["delays", "cancellations", "cancelled", "\"ground stop\"", "strike", "outage",
                    "closure", "shutdown", "evacuated", "\"air traffic\""]


MAX_QUERY_CHARS = 240  # GDELT answers longer queries with "Your query was too short or too long"
NATIONAL = ['"air traffic control"', "FAA", "TSA"]


def _query(places: list[str]) -> str:
    return f"({' OR '.join(places)}) ({' OR '.join(DISRUPTION_TERMS)}) sourcelang:english"


def build_query(origin: Place, dest: Place, origin_aps: list[Airport], dest_aps: list[Airport],
                minimal: bool = False) -> str:
    """Airport/city phrases + disruption words, most important first, trimmed to GDELT's length limit.

    Order: the main airport on each side, the national system, then the alternates (e.g. LGB, BUR).
    `minimal` keeps only the main airport codes and FAA, for a retry after GDELT rejects a query.
    """
    main = [ap for ap in (origin_aps[:1] + dest_aps[:1])]
    rest = origin_aps[1:] + dest_aps[1:]

    def phrases(ap: Airport, with_city: bool = True) -> list[str]:
        out = [f'"{ap.iata} airport"']
        if with_city and ap.city:  # hyphens ("Dallas-Fort Worth") confuse GDELT's phrase parser
            out.append(f'"{ap.city.replace("-", " ")} airport"')
        return out

    if minimal:
        return _query([p for ap in main for p in phrases(ap, with_city=False)] + ["FAA"])
    ordered = [p for ap in main for p in phrases(ap)] + NATIONAL + [p for ap in rest for p in phrases(ap)]
    places: list[str] = []
    for p in dict.fromkeys(ordered):
        if len(_query(places + [p])) > MAX_QUERY_CHARS:
            break
        places.append(p)
    return _query(places)


def query_rejected(err: Exception) -> bool:
    return "too short or too long" in str(err)


NEWS_TIMEOUT_S = 4  # news is one signal among many and the first answer waits on it: give up quickly
MIN_INTERVAL_S = 6  # GDELT allows one request per 5 seconds; a little margin
_gate = asyncio.Lock()
_last_call = 0.0


async def fetch(query: str, *, timespan: str = "3d", max_records: int = 25) -> list[dict]:
    global _last_call
    async with _gate:  # space out calls so we don't trip GDELT's rate limit
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            text = await http.get(URL, params={
                "query": query, "mode": "ArtList", "format": "json", "maxrecords": max_records,
                "timespan": timespan, "sort": "DateDesc",
            # fail fast: news is one signal among many, so a slow or rate-limited GDELT
            # shows as "Unavailable" instead of holding up the whole assessment
            }, as_json=False, ttl=600, timeout=NEWS_TIMEOUT_S, retries=0)
        finally:
            _last_call = time.monotonic()
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as e:  # GDELT answers query errors with plain text
        raise ValueError(f"GDELT returned a non-JSON response: {text.strip()[:160]}") from e
    seen, articles = set(), []
    for a in data.get("articles") or []:
        title = (a.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        articles.append({
            "title": title, "url": a.get("url"), "domain": a.get("domain"),
            "seendate": a.get("seendate"),
        })
    return articles


SEARCH_BUDGET_S = 15  # hard ceiling: a blocked or slow GDELT must not hold up the assessment


async def search(origin: Place, dest: Place, origin_aps: list[Airport], dest_aps: list[Airport]) -> list[dict]:
    """Fetch with the full query; if GDELT rejects its length, retry once with the minimal one."""
    async def run() -> list[dict]:
        try:
            return await fetch(build_query(origin, dest, origin_aps, dest_aps))
        except ValueError as e:
            if not query_rejected(e):
                raise
            return await fetch(build_query(origin, dest, origin_aps, dest_aps, minimal=True))

    try:
        return await asyncio.wait_for(run(), SEARCH_BUDGET_S)
    except (asyncio.TimeoutError, httpx.TimeoutException, httpx.ConnectError):
        raise ConnectionError("GDELT didn't respond (it limits how often it can be queried). "
                              "News wasn't checked this time.") from None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            raise ConnectionError("GDELT is rate-limiting requests right now. News wasn't checked this time.") from None
        raise


# ---------------------------------------------------------------- Google News (primary)
# Google News' RSS search is free and keyless, answers quickly and has no tight rate limit,
# so it's tried first; GDELT (above) is the backup.
GOOGLE_URL = "https://news.google.com/rss/search"


ROAD_TERMS = ["closure", "closed", "crash", "flooding", "evacuated", "outage", "\"road work\""]


def google_query(origin_aps: list[Airport], dest_aps: list[Airport], cities: list[str] | None = None) -> str:
    """Trip airports/cities AND a disruption word, last 3 days. Drives (no airports) search the cities."""
    if not origin_aps and not dest_aps and cities:
        names = [f'"{c}"' for c in dict.fromkeys(cities)]
        return f"({' OR '.join(names)}) ({' OR '.join(ROAD_TERMS)}) when:3d"
    places: list[str] = []
    for ap in origin_aps[:1] + dest_aps[:1] + origin_aps[1:] + dest_aps[1:]:
        places.append(f'"{ap.iata} airport"')
        if ap.city:
            places.append(f'"{ap.city.replace("-", " ")} airport"')
    # national: ATC staffing/outages hit every flight; bare "TSA" was dropped (it floods results with trivia)
    places = list(dict.fromkeys(places + ['"air traffic control"']))[:12]
    return f"({' OR '.join(places)}) ({' OR '.join(DISRUPTION_TERMS)}) when:3d"


def parse_google_rss(xml_text: str) -> list[dict]:
    """RSS items -> the same article dicts GDELT produces (title, url, domain, seendate)."""
    root = ET.fromstring(xml_text)
    seen, out = set(), []
    for it in root.iter("item"):
        source = (it.findtext("source") or "").strip()
        title = (it.findtext("title") or "").strip()
        if source and title.endswith(f" - {source}"):  # Google appends " - Publisher" to titles
            title = title[: -len(source) - 3].strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            when = None
        out.append({"title": title, "url": it.findtext("link"), "domain": source or "Google News", "seendate": when})
    return out[:25]


MAX_AGE = timedelta(days=3)


async def google_search(origin_aps: list[Airport], dest_aps: list[Airport], cities: list[str]) -> list[dict]:
    text = await http.get(GOOGLE_URL, params={"q": google_query(origin_aps, dest_aps, cities), "hl": "en-US",
                                              "gl": "US", "ceid": "US:en"},
                          as_json=False, ttl=600, timeout=NEWS_TIMEOUT_S, retries=0)
    # Google doesn't reliably honour when:3d, so old stories are dropped here
    cutoff = (datetime.now(timezone.utc) - MAX_AGE).isoformat()
    return [a for a in parse_google_rss(text) if a["seendate"] and a["seendate"] >= cutoff]


KEYWORDS = ("delay", "cancel", "ground stop", "strike", "outage", "closure", "closed", "shutdown",
            "evacuat", "staffing", "storm", "snow", "fog")


def keyword_relevance(articles: list[dict], terms: list[str]) -> list[dict]:
    """Fallback when Gemini isn't available: keep headlines naming a trip location + disruption word."""
    out = []
    for i, a in enumerate(articles):
        t = a["title"].lower()
        if any(k in t for k in KEYWORDS) and any(term.lower() in t for term in terms):
            out.append({"index": i, "relevant": True, "severity": 1,
                        "reason": "Headline mentions a trip location and a disruption keyword (keyword match, not AI-verified)."})
    return out
