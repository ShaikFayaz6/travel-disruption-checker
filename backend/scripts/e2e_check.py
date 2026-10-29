"""End-to-end check of a running Trip Check: the page, the /api proxy and the backend together.

The same checks for development and production; point it at the frontend:
    cd backend && python -m scripts.e2e_check                         # dev:    npm run dev + uvicorn
    cd backend && python -m scripts.e2e_check http://localhost:3000   # Docker: docker compose up
    python -m scripts.e2e_check --flights                             # also the flight lookup (uses AeroDataBox quota)

Uses live data (Gemini quota for one check). Exit code 0 = every check passed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta

import httpx

results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base", nargs="?", default="http://localhost:3000", help="the frontend's URL")
    p.add_argument("--flights", action="store_true", help="also check the flight lookup (AeroDataBox quota)")
    args = p.parse_args()
    base = args.base.rstrip("/")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    c = httpx.Client(base_url=base, timeout=150)
    print(f"Trip Check end to end at {base}")

    # 1. the page
    try:
        r = c.get("/")
        check("page loads", r.status_code == 200 and "Trip Check" in r.text, f"HTTP {r.status_code}")
    except httpx.HTTPError as e:
        check("page loads", False, f"{e.__class__.__name__}: is the frontend running at {base}?")
        return 1

    # 2. the proxy reaches the backend
    r = c.get("/api/health")
    health = r.json() if r.status_code == 200 else {}
    check("/api proxied to the backend", health.get("ok") is True, f"HTTP {r.status_code}")
    ai_on, flights_on = health.get("ai_enabled"), health.get("flight_lookup")
    print(f"        (Gemini {'on' if ai_on else 'off'}, flight lookup {'on' if flights_on else 'off'})")

    # 3. the airport and city lists are loaded
    r = c.get("/api/places", params={"q": "Denv"})
    found = r.json().get("results", []) if r.status_code == 200 else []
    check("place suggestions (city and airport lists)", any("Denver" in x["label"] for x in found), f"{len(found)} results")

    # 4. a check, streamed: the rules answer first, then the AI's; times in the viewer's zone
    body = {"origin": "Denton, TX", "destination": "Austin, TX", "date": tomorrow, "time": "09:00",
            "mode": "drive", "display_tz": "America/Los_Angeles"}
    answers, t0, firsts = [], time.perf_counter(), []
    with c.stream("POST", "/api/assess/stream", json=body) as r:
        check("streamed check starts", r.status_code == 200 and "ndjson" in r.headers.get("content-type", ""),
              f"HTTP {r.status_code}")
        for line in r.iter_lines():
            if line.strip():
                answers.append(json.loads(line))
                firsts.append(round(time.perf_counter() - t0, 1))
    final = answers[-1] if answers else {}
    expected = 2 if ai_on else 1
    check("answers arrive one by one", len(answers) == expected and "error" not in final,
          f"{len(answers)} answer(s) at {firsts} s")
    if ai_on and len(answers) == 2:
        check("first answer is the rules one, then the AI's",
              answers[0].get("ai_pending") is True and final.get("ai_pending") is False,
              f"last by {final.get('assessment', {}).get('generated_by')}")
    t = final.get("timing") or {}
    check("times shown in the viewer's zone", str(t.get("departure", "")).endswith(("PDT", "PST")),
          f"departs {t.get('departure')}")
    check("leave time read in the viewer's zone", (final.get("request") or {}).get("time", "").startswith("11:00"),
          "09:00 Pacific is 11:00 in Denton")
    check("evidence and sources present", bool(final.get("signals")) and bool(final.get("sources")),
          f"{len(final.get('signals', []))} signals, {len(final.get('sources', []))} sources")
    check("timings reported", bool((final.get("timings") or {}).get("calls")),
          f"{(final.get('timings') or {}).get('total_ms')} ms on the server")

    # 5. errors come back as messages through the proxy
    r = c.post("/api/assess", json={**body, "date": "2020-01-01"})
    check("a bad request gets a clear error", r.status_code == 400 and "past" in r.json().get("detail", ""),
          f"HTTP {r.status_code}: {r.json().get('detail', '')[:60]}")

    # 6. optional: flight lookup
    if args.flights:
        if not flights_on:
            check("flight search", False, "AERODATABOX_API_KEY not set in the backend")
        else:
            r = c.get("/api/flights", params={"origin": "Los Angeles, CA", "destination": "New York, NY",
                                              "date": tomorrow, "time": "08:00", "display_tz": "America/Chicago"})
            n = len(r.json().get("results", [])) if r.status_code == 200 else 0
            check("flight search", r.status_code == 200 and n > 0, f"{n} flights")

    failed = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
