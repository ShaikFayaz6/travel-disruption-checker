# Trip Check

**Will anything get in the way of this trip?**

People who travel for work often have to check many websites to know if their trip is safe: the weather, the airport, the news. Trip Check does it in one place.

You type where you are going, when, and how (by air or by car). Trip Check looks at live data from many free sources and tells you:

- **Is there a real risk?** A clear status: *Clear to go*, *Watch closely* or *Disruption likely*.
- **What are the risks?** Short, plain sentences, for example "Snow at Denver when you land".
- **What is the proof?** Every risk links to the exact data behind it: who said it, where, and for which hours.
- **What should you do?** Simple actions, sorted by when to do them: *Now*, *Before you go* or *On the day*.

All data is real and live. Nothing is made up or mocked. An AI (Google Gemini, free tier) reads the evidence and writes the summary. The risk rating itself always comes from fixed, simple rules.

---

## How this meets the brief

| The brief asks for | How Trip Check does it | Where to look |
|---|---|---|
| **At least three external sources** | 13 free sources: weather forecasts, official alerts, airport forecasts and observations, live FAA status, news, airports, places and flight schedules | [Where the data comes from](#where-the-data-comes-from), `backend/app/sources/` |
| **Real data, no mocked responses** | Every check calls the live services. Fake data is used only in the offline unit tests, and a separate test checks that every live source answers | `backend/scripts/smoke_test.py` |
| **AI in a meaningful part** | Gemini decides which news headlines really affect this trip, and writes the assessment from the evidence. Rules set the rating, so the AI explains it but can't change it | [Where AI is used](#where-ai-is-used-and-its-safety-rules), `backend/app/ai.py` |
| **A working, end-to-end experience** | Enter a trip, pick your flights (optional), and get a rated assessment with your door-to-door timeline. A test checks the running app as a user sees it | `backend/scripts/e2e_check.py` |
| **Clear evidence behind recommendations** | Every risk links to the evidence behind it: which source, which place, which hours, and a link to the original data | `frontend/components/Findings.tsx`, `Evidence.tsx` |
| **Is there a meaningful risk? What is it? What should I do?** | A status (*Clear to go*, *Watch closely*, *Disruption likely*), the risks in plain words, and up to 3 actions sorted by when to do them | [Read the result](#4-read-the-result) |

---

## Key decisions and trade-offs

- **Rules set the rating; the AI explains it.** The Operations team needs the same data to give the same answer, and needs to be able to check why. An AI can answer differently each time and can't be audited, so I kept it away from the rating. The AI does what rules do badly: judging news and writing clearly.
- **AI only where judgement on text is needed.** Weather, alerts and airport status come as structured data, so fixed rules handle them reliably. Headlines don't: a keyword search can't tell "ground stop at O'Hare" from a story that only mentions Chicago. That is where I used the AI.
- **Check the hours you are actually there, not the whole day.** Checking the whole travel day raises false alarms, such as an evening storm against a morning flight that has already left. So I build a timeline of the trip and check each place only for the hours the traveller is there.
- **Prefer the sources airlines use.** Airport forecasts (TAF), live FAA programs and official NWS alerts say more about flight disruption than a general weather app, so I built the check around them. Open-Meteo adds hour-by-hour detail up to 16 days out, where those don't reach.
- **Show a result fast, then improve it.** The free AI takes 5–25 s. I show the rules' result in about 3 s and replace the text when the AI's answer arrives, so the user is never left looking at a spinner.
- **If a source fails, say so; don't stop.** Free services fail often. The check always finishes, lists what was unavailable and why, and its confidence takes the missing data into account.
- **Free sources only, as the brief requires.** The trade-off: Google News is meant for personal use, and live flight status needs a paid plan. I list both under [what I would change](#what-is-missing-and-what-i-would-change-for-real-users).

The assumptions I made (flight speed, drive speed, time at the airport) are listed in [How a check works](#the-guesses-i-make-and-say-out-loud).

---

## Contents

1. [How this meets the brief](#how-this-meets-the-brief)
2. [Key decisions and trade-offs](#key-decisions-and-trade-offs)
3. [What you can do with it](#what-you-can-do-with-it)
4. [How to run it](#how-to-run-it)
5. [How to test it](#how-to-test-it)
6. [Where the data comes from](#where-the-data-comes-from)
7. [How a check works, step by step](#how-a-check-works-step-by-step)
8. [How the risk rating is decided](#how-the-risk-rating-is-decided)
9. [Where AI is used, and its safety rules](#where-ai-is-used-and-its-safety-rules)
10. [How I show what I am not sure about](#how-i-show-what-i-am-not-sure-about)
11. [Speed](#speed)
12. [Files in the project](#files-in-the-project)
13. [What is missing, and what I would change for real users](#what-is-missing-and-what-i-would-change-for-real-users)

---

## What you can do with it

### 1. Enter your trip

- **From and To.** Start typing a city or an airport code. A list of suggestions appears straight away, and small typing mistakes are fine ("danton" finds Denton, TX). You must pick from the list, so the tool always knows the exact place.
- **Travel date and "Leave at".** The time you leave your home or office. It is **your own local time**, wherever you are. The hint under the field shows your time zone and the time now. If the city you leave from is in another time zone, it shows that city's time too.
- **Travelling by.** *Air* (the default) or *Car*.
- **Try an example.** Three ready-made trips fill in the form and run a check in one click.

### 2. Choose your real flights (Air only)

You don't have to pick a flight. If you don't, the tool assumes a direct flight at normal speed, and says clearly that this is a guess. Choosing your real flights makes the check much more exact.

- **The flight list loads by itself** once From, To, date and time are filled in. It only shows flights you can still catch after driving to the airport and spending 1 hour there.
- **Connections.** Add a stop with "connect via [airport]". The next search starts from that airport, 40 minutes after you land.
- **Enter a flight yourself.** Type a flight number such as `wn3872`. It is tidied to `WN 3872`, looked up, and the airports and times are filled in for you. Only *From* and *Departs* are needed. If you leave *Arrives* empty, it is estimated from the distance and marked as an estimate.
- **Done, check these flights.** A trip can have several flights, so nothing is checked until you press *Done*. The tool then works out when you need to leave and puts that time into the form.
- **Mistakes are caught.** The tool explains the problem in plain words when:
  - two flights don't connect;
  - a flight leaves before the one before it lands;
  - the last flight lands far from where you are going;
  - your trip lands at your destination's airport and then flies away again (for example, to Denver: land at DEN, then fly on to Colorado Springs).

### 3. See the whole journey, door to door

The top of the result is a line that shows your full trip:

- **Car** from your city to the airport, with the driving time.
- **1 hour at the airport** for check-in, bag drop and security.
- **Each flight** with a plane icon, its flight number and its time in the air. Layovers are marked between flights.
- **30 minutes for bags** after landing.
- **Car** from the airport to where you are going.

Below it is a step-by-step list, for example "Leave Millcreek by 5:45 AM", the drive, each flight, the layover, baggage claim, and when you reach your destination.

For car trips, the line shows the total driving time, including overnight stops on very long drives.

### 4. Read the result

- **Status and summary.** For example *Clear to go: Favorable travel conditions*.
- **A better time to leave.** If the weather is bad at your time, the tool tries leaving up to 6 hours earlier or later and suggests the nearest time that looks clearer.
- **What could go wrong.** Each risk has a level (*Low risk*, *Moderate risk*, *High risk*) and buttons that jump to its evidence.
- **What to do.** Up to 3 simple actions, sorted by when to do them.
- **Evidence.** The data that raised a concern. Everything else that was checked and found normal is in a separate list, sorted in trip order, with the hours you will be at each place.
- **How sure is this.** The confidence level, *why* (for example "Near-term forecasts and live airport data are available"), and what could still change.
- **Sources.** Every source used, marked *Checked* or *Unavailable* (with the reason). Airport forecasts (TAF) show *No data* instead when they fail, come back empty or don't cover your hours.

### 5. All times in your own time zone

Every time on the page is shown in **your** time zone, so someone in Chicago sees CDT and someone in Los Angeles sees PDT for the same trip. The only exception is the flight list, which shows each airport's local time, the same as on a ticket.

---

## How to run it

You need:
- **Python 3.13** and **Node 24**: the same versions the Docker images use.
- **A free Gemini key** (optional): from https://aistudio.google.com/apikey.
- **A free AeroDataBox key** (optional): for the flight lookup. Sign up on rapidapi.com, find AeroDataBox and choose the free *Basic* plan.

Without the keys the app still works. You get a rule-based summary instead of the AI one, and you type your flights in by hand.

### Option A: on your computer

```bash
# 1. Backend (the API), in one terminal
cd backend
py -m venv venv
.\venv\Scripts\Activate.ps1                         # on Windows: .venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt # exact package versions
cp .env.example .env                               # then paste your keys into .env
uvicorn app.main:app --reload --port 8000

# 2. Frontend (the web page), in a second terminal
cd frontend
npm install
npm run dev                                        # open http://localhost:3000
```

The web page sends every `/api/...` request on to the backend at `http://localhost:8000`, so you only ever open one address.

### Option B: with Docker

You need Docker with Compose v2.24 or newer (Docker Desktop on Windows and Mac).

```bash
cp backend/.env.example backend/.env   # paste your keys (optional)
docker compose up --build              # open http://localhost:3000
docker compose logs -f backend         # see the speed table for each check
docker compose down                    # stop (add -v to also delete the saved data)
```

What the Docker setup does:
- **Two containers:** one for the backend and one for the web page. The web page starts only after the backend says it is healthy.
- **Your keys stay private.** They are read from `backend/.env` when the app starts, and are never copied into an image.
- **The city and airport lists** (about 17 MB) are downloaded once and kept in a Docker volume, so later starts are fast.
- **Same versions as on your computer.** Python 3.13, Node 24, and the same package versions (`backend/constraints.txt` and `frontend/package-lock.json`).

---

## How to test it

The same tests work on your computer and in Docker.

| Test | What it checks | On your computer | In Docker |
|---|---|---|---|
| **Unit tests** (64) | Each part on its own, offline, with fake data: parsers, rules, timing, flights, AI handling | `cd backend && pytest` | `docker compose exec backend pytest` |
| **Type check** | The web page code has no type errors | `cd frontend && npm run typecheck` | runs while the image is built |
| **Live sources** | Every real data source answers | `cd backend && python -m scripts.smoke_test` | `docker compose exec backend python -m scripts.smoke_test` |
| **End to end** | The running app as a user sees it: page, API, live check, streaming, time zones, error messages | `cd backend && python -m scripts.e2e_check` | the same command, while `docker compose up` is running |

Add `--flights` to the end-to-end test to also test the flight lookup (this uses some of the free AeroDataBox quota).

Tip: don't run `npm run build` while `npm run dev` is running, because both use the same `.next` folder.

---

## Where the data comes from

I use 13 free sources. None of them needed to be paid for.

| Source | What it tells the tool | When it is used |
|---|---|---|
| **GeoNames** city list | Every US place with 1,000+ people, for the type-ahead suggestions | Always |
| **Open-Meteo geocoding** | The location and time zone of a place that isn't in the city list | Backup |
| **OurAirports** | Which airports serve a city (New York → JFK, LGA, EWR) | Air trips |
| **Open-Meteo forecast** | Hour-by-hour snow, wind gusts, rain, storms, freezing rain and fog | Up to 16 days ahead |
| **National Weather Service alerts** | Official weather warnings and advisories | Always |
| **National Weather Service forecast** | The official written forecast, as extra support | Up to 7 days ahead |
| **AviationWeather.gov TAF** | Official airport forecasts that pilots use: low cloud, poor visibility, storms | Today and tomorrow |
| **AviationWeather.gov METAR** | The weather at each airport right now | Today |
| **FAA airport status** | Live ground stops, delay programs and closures | Most useful on the day |
| **Google News** | Recent headlines about delays, strikes, outages, staff shortages, or road closures | Always |
| **GDELT news** | The same kind of headlines | Only if Google News fails |
| **AeroDataBox** (optional key) | Real flight schedules, and lookup by flight number | Air trips |
| **Google Gemini** (optional key) | Reads the evidence and writes the summary | Always, if a key is set |

If a source fails, the check still finishes. The failed source is shown as *Unavailable* with the reason. The exception is airport forecasts (TAF), shown as *No data available for that period*.

---

## How a check works, step by step

1. **Find the places.** Turn the From and To into exact locations, and find their airports.
2. **Build a timeline.** Work out when you will be at each place: leaving home, at the airport, in the air, at each connection, landing, and arriving. For car trips, add points along the road about every 150 miles (up to 4 points), each with its own time zone. Long drives get overnight stops (up to 10 hours of driving a day).
3. **Check only the hours that matter.** Each place is checked only for the hours you are actually there. A storm in the evening does not count against a morning flight that has already left.
   - Departure airport: from 3 hours before take-off to 1 hour after.
   - Each connection: from 1 hour before you land to 1 hour after your next flight leaves.
   - Arrival airport: from 1 hour before landing to 2 hours after.
   - Drives: around the time you pass each point.
4. **Ask all sources at the same time**, so the check is fast.
5. **Turn the answers into evidence.** Each piece of data becomes one evidence item with a level: context, low, moderate or high. Each item also says *where* it applies, for *which hours*, and whether it really affects *your* travel date.
6. **Rate the risk** with fixed rules (see below).
7. **Show the result straight away**, then let the AI write a better summary.

### The guesses I make, and say out loud

- **A flight you didn't pick:** a direct flight at about 470 mph, plus about 36 minutes for taxi, take-off and landing. The page shows a clear note that this is an assumption (for example, a private jet or an unlisted flight).
- **Driving:** about 62 mph on roads about 20% longer than a straight line.
- **Short trips by air:** with no flight chosen, if these guesses make driving quicker door to door, the page says so and offers *Check by car instead*. Air stays your choice, because you may have a private or charter flight. Private jets and helicopters aren't fully covered: the times assume an airline-style trip.
- **Driving to and from the airport:** about 50 mph.
- **At the airport:** 1 hour before take-off for check-in and bags, and 30 minutes after landing for baggage claim.

---

## How the risk rating is decided

The rating comes from simple, fixed rules, not from the AI. This means the same data always gives the same rating, and anyone can check it.

- Only evidence that affects **your travel date and hours** counts.
- Each source counts **once per part of the trip** (departure, route, arrival): if three New York airports report the same storm in their forecasts, it counts once. Different sources that agree (an official alert *and* the hourly forecast) each count, because agreement makes the risk more certain.
- Points: low = 1, moderate = 3, high = 6.
- **Disruption likely (high):** any high-level evidence, or 8 points or more.
- **Watch closely (medium):** any moderate evidence, or 3 points or more.
- **Clear to go (low):** anything less.

Some evidence counts less when it is far away in time. For example, a live FAA ground stop matters a lot if you fly in 2 hours, but is only background if you fly next week.

---

## Where AI is used, and its safety rules

The AI (Google Gemini) has two jobs, and does both in **one** request:

1. **Pick the news that matters.** A news search returns many headlines. The AI decides which ones really affect *this* trip, and how much. For example, it keeps "Ground stop at Chicago O'Hare" and drops "Celebrity wedding in Paris".
2. **Write the summary.** It turns the evidence into the headline, the risks, the actions and a short note on what could change.

**Safety rules:**
- The AI **only sees the evidence the tool collected**. It is told not to add anything else.
- Every risk it writes must **point to real evidence**. Any risk without proof is removed by the code, and a risk can never be rated worse than the evidence it points to.
- **At most 3 actions.** Extra actions are cut by the code, not just by asking the AI.
- **Headlines are treated as untrusted data.** Each headline is sent as a quoted string, and the AI is told to ignore any instructions inside one. Even if a headline fooled it, the rating would not change, because the rules set it.
- **Only web links are shown.** A news link that isn't `http(s)` is dropped.
- The AI **cannot change the rating**. If the news it picks changes the rating (for example from low to medium), the summary is written again so the words always match the rating.
- News on its own can never be rated *high*.
- **If the AI fails**, the rules write a simple summary instead, and the page says why the AI text is missing (for example "daily quota used up, back in 2 hours").

**Making the AI fast and reliable:**
- **No waiting on a full quota.** The free tier allows 20 requests a day for each model. When one model's quota runs out, the tool goes straight to the next model and skips the full one until Google says it is available again.
- **No "thinking" time.** These jobs don't need the AI to reason first, so I ask it to answer directly. If a model doesn't accept that setting, the request is simply sent again without it.

---

## How I show what I am not sure about

- **Confidence** (high, medium or low) always comes with a reason, for example how far away the trip is and which sources failed.
- **A note on timing** explains which data is useful for your date. For example, live airport status only matters on the day, and airport forecasts only cover about 30 hours.
- **Every guess is labelled.** Estimated arrival times, assumed flights and drive times are marked as estimates, with the numbers used.
- **Evidence that doesn't affect your trip** (for example today's FAA status for a trip next week) is shown separately and does not count toward the rating.
- **Every evidence item** shows when the data was made and which hours it covers.
- **Every source** is listed as *Checked*, *Unavailable* (with the reason) or, for airport forecasts (TAF), *No data*.

---

## Speed

**Why it can be slow:** the wait comes from the outside services, all on free tiers. The app's own work takes under a second. Measured on the live site:

| Service | Answer time | Why |
|---|---|---|
| Gemini AI | 5–25 s | Free-tier queue: about 9 s before the first word, then 0.5 s to write the answer |
| AviationWeather.gov | up to 10 s | Slow at times |
| Google News, GDELT | timeouts | Often don't answer cloud servers |
| Weather, NWS, FAA | under 1 s | |

Paid tiers would remove most of this wait. For a free-tier project, I think it is acceptable.

**How I keep the wait short:**
- **The result appears first, in about 3 s.** The rules' rating and evidence show first; the AI summary replaces them when ready.
- **Slow sources get 3 s.** AviationWeather.gov and news don't hold up the result past 3 s; anything later still reaches the AI. On the live site this took the first result from 9.2 s to 3.4 s.
- **All sources are asked at once.** A news source that failed is skipped for 10 minutes, so it doesn't slow down the next checks.
- **The AI's work is kept small.** One AI request does both jobs, with no "thinking" time, and the prompt is 30% smaller. Full-quota models are skipped.
- **Data is cached** for a few minutes (news 10 minutes, forecasts 30 minutes), so checking the same trip again is almost instant.
- **I tested streaming the AI text and didn't use it.** It would only save about 0.4 s, because writing isn't the slow part.
- **Speed report.** Every check prints a table showing how long each data source took: when it started, how long until the first answer, the full time, and whether it came from the cache.
  - **On your computer:** it appears in the backend terminal.
  - **In the browser:** the same table is in the developer console (press F12 and look for `[Trip Check]`).

---

## Files in the project

```
backend/                     The API (Python, FastAPI)
  app/main.py                The web addresses (API endpoints)
  app/assess.py              Runs a check from start to finish
  app/timing.py              The trip timeline: drives, airport time, flights, windows
  app/scoring.py             The fixed rules for the rating and confidence
  app/ai.py                  Gemini: prompts, safety checks, fallback, quota handling
  app/perf.py                The speed report
  app/sources/               One file for each data source
  tests/                     64 offline unit tests
  scripts/smoke_test.py      Checks every live source
  scripts/e2e_check.py       Tests the running app end to end
  constraints.txt            Exact package versions
  Dockerfile
frontend/                    The web page (Next.js, React)
  app/page.tsx               The main page
  components/                The form, flight picker, route line, results, evidence, side notes
  lib/types.ts               Data shapes and API calls
  Dockerfile
docker-compose.yml           Runs both with one command
```

**Main API endpoints:**
- `POST /api/assess/stream`: a check, sent in two parts (rules first, then AI). Used by the page.
- `POST /api/assess`: the same check, as one finished answer.
- `GET /api/flights`: flights you can catch between two places.
- `GET /api/flight`: look up one flight number.
- `GET /api/places`: suggestions for the From and To fields.
- `GET /api/health`: is the backend running, and are the keys set.

---

## What is missing, and what I would change for real users

**What is missing today:**
- **Live flight status.** I use real flight *schedules*, but I don't track a single flight's live delay or cancellation, because the free plan has too few requests for that. A paid feed (such as FlightAware) would add it.
- **The real road.** Car trips are checked along a straight line between the two cities, not the actual roads. A routing service and state road-closure feeds would fix this.
- **Past delay history.** Some airports are delayed more often than others. US government on-time data could add this.
- **News is headlines only.** Google News is meant for personal use. A real product would need a paid news service.
- **One trip at a time.** Nothing is saved.

**Before giving it to real users, I would:**
- Build **a view for the Operations team.** The brief's customer is the Ops team, but today the tool checks one trip at a time for one traveller. I would add a page listing everyone travelling in the next few days, sorted by risk, so Ops can see at a glance who may be affected and contact them before a problem starts.
- Add **login**, and **save trips** for each person.
- **Re-check trips automatically** 24 hours and 3 hours before departure, and send an alert (email, Slack or Teams) when the rating changes.
- Move to **paid or licensed data** where the free terms don't allow business use (news, flight status), and add the missing sources above.
- Add **limits per user** so one person can't use up the shared AI and flight quotas.
- Add **monitoring** for failing sources and slow checks, using the speed report the tool already has.
- **Test with real travellers and the operations team**, and adjust the rules and wording based on what they find useful.
