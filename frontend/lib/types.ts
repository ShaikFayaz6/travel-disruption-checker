export type RiskLevel = "low" | "medium" | "high";
export type Mode = "auto" | "fly" | "drive";

export interface Place {
  query: string;
  name: string;
  state?: string | null;
  lat: number;
  lon: number;
  timezone?: string | null;
}

export interface Airport {
  iata: string;
  icao?: string | null;
  name: string;
  city?: string | null;
  state?: string | null;
  distance_miles: number;
}

export interface Signal {
  id: string;
  source: string;
  category: "weather" | "airport_ops" | "aviation_weather" | "news" | "road";
  side: "origin" | "destination" | "route" | "national";
  location: string;
  severity: 0 | 1 | 2 | 3;
  title: string;
  detail: string;
  observed_at?: string | null;
  valid_for?: string | null;
  url?: string | null;
  applies_to_travel_date: boolean;
}

export interface SourceStatus {
  name: string;
  status: "ok" | "error" | "skipped";
  detail: string;
  latency_ms?: number | null;
  url?: string | null;
}

export interface RiskItem {
  title: string;
  severity: "low" | "moderate" | "high";
  explanation: string;
  evidence_ids: string[];
}

export interface Action {
  action: string;
  why: string;
  priority: "now" | "before_trip" | "day_of" | "optional";
}

export interface Assessment {
  headline: string;
  summary: string;
  risks: RiskItem[];
  actions: Action[];
  uncertainty: string;
  generated_by: "gemini" | "rules_fallback";
  model?: string | null;
  ai_error?: string | null;
}

export interface Segment {
  flight?: string | null; // null: an estimated flight
  dep_iata: string;
  arr_iata: string;
  minutes: number; // in the air
  estimated: boolean;
  layover_min?: number | null; // at arr_iata before the next flight
}

export interface Timing {
  departure: string;
  est_arrival: string;
  duration_hours: number;
  moving_hours: number;
  overnight_stops: number;
  hours_until_departure: number;
  windows: Record<string, string>;
  basis: string;
  from_flights: boolean;
  legs: string[];
  segments: Segment[];
  leave_by?: string | null; // flying: leave the origin city
  leave_local?: string | null; // the same as "YYYY-MM-DDTHH:MM" on the origin's clock
  at_airport_by?: string | null;
  out_of_airport?: string | null; // after 30 min for bags
  door_arrival?: string | null;
  drive_to_airport?: string | null;
  drive_from_airport?: string | null;
  drive_to_min?: number | null;
  drive_from_min?: number | null;
  better_departure?: string | null;
  est_flight?: string | null; // no flight chosen: the assumed nonstop
  cruise_mph?: number | null;
  overhead_min?: number | null;
  flight_miles?: number | null;
}

export interface AssessResponse {
  request: { origin: string; destination: string; date: string; time?: string | null; mode: Mode };
  timing?: Timing | null;
  origin: Place;
  destination: Place;
  mode: "fly" | "drive";
  distance_miles: number;
  days_out: number;
  horizon_note: string;
  origin_airports: Airport[];
  destination_airports: Airport[];
  risk_level: RiskLevel;
  risk_score: number;
  confidence: "low" | "medium" | "high";
  confidence_reason: string;
  assessment: Assessment;
  signals: Signal[];
  sources: SourceStatus[];
  generated_at: string;
  timings?: Timings | null;
  ai_pending?: boolean; // the quick first answer: the AI's summary and news check are still coming
}

export interface PlaceSuggestion {
  value: string; // what gets sent to /api/assess, e.g. "Denton, TX" or "DFW"
  label: string;
  detail: string;
  kind: "city" | "airport";
  timezone?: string | null; // IANA zone, so the form can show the local time there
}

/** Backend timing report (backend/app/perf.py): each API call and each step of one request. */
export interface Timings {
  total_ms: number;
  fetched: number;
  cached: number;
  steps: Array<{ step: string; start_ms: number; ms: number }>;
  per_api: Array<{ api: string; calls: number; cached: number; max_ms: number; sum_ms: number }>;
  calls: Array<{ api: string; where: string; start_ms: number; latency_ms: number | null; fetch_ms: number;
    status: number | string; kb: number | null; cache: string }>;
}

/**
 * Prints one request's timings to the browser console. latency = time to the first byte of the
 * API's answer, fetch = the whole call; start = when it began (calls with close starts ran in parallel).
 */
function logTimings(what: string, roundTripMs: number, t?: Timings | null) {
  if (!t) return;
  console.groupCollapsed(`[Trip Check] ${what}: ${Math.round(roundTripMs)} ms in the browser, ${t.total_ms} ms on the server ` +
    `(${t.fetched} API calls fetched, ${t.cached} from cache)`);
  if (t.steps.length) console.table(t.steps);
  console.table(t.per_api);
  console.table(t.calls);
  console.groupEnd();
}

/** The viewer's IANA time zone, e.g. "America/Chicago": every time on the page is shown in it. */
export function viewerTz(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export async function suggestPlaces(q: string): Promise<PlaceSuggestion[]> {
  const res = await fetch(`/api/places?q=${encodeURIComponent(q)}`);
  if (!res.ok) throw new Error(`Suggestions returned ${res.status}`);
  return (await res.json()).results;
}

/** One flight; times are "YYYY-MM-DDTHH:MM" on each airport's own clock, as printed on a booking. */
export interface FlightLeg {
  flight?: string | null;
  dep_iata: string;
  arr_iata?: string | null; // blank: the airport nearest the destination (or the next flight's airport)
  dep_local: string;
  arr_local?: string | null; // blank: estimated from the distance
}

export interface FlightOption extends FlightLeg {
  flight: string;
  airline: string;
  arr_iata: string;
  arr_local: string;
}

export interface FlightSearch {
  results: FlightOption[];
  origin_airports: string[];
  destination_airports: string[];
  earliest: Record<string, string>; // per origin airport: the first departure you can make, local
}

/**
 * Scheduled flights from origin to destination you can catch. fromHome: date+time is when you
 * leave the origin city (the drive and an hour at the airport are added); otherwise it's the
 * earliest departure itself (a connection).
 */
export async function findFlights(origin: string, destination: string, date: string, time: string,
  fromHome = true): Promise<FlightSearch> {
  // from home, date+time is the form's Leave at: on the viewer's clock. A connection's is the airport's.
  const q = new URLSearchParams({ origin, destination, date, time, from_home: String(fromHome),
    ...(fromHome ? { display_tz: viewerTz() } : {}) });
  const t0 = performance.now();
  const res = await fetch(`/api/flights?${q}`);
  if (!res.ok) {
    let msg = `Flight lookup returned ${res.status}.`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
    } catch {
      /* keep the generic message */
    }
    throw new Error(msg);
  }
  const data = await res.json();
  logTimings(`Flight search ${origin} → ${destination}`, performance.now() - t0, data.timings);
  return data;
}

/** "wn3872" -> "WN 3872" (capitals, a space between airline code and number); null if it isn't one. */
export function normalizeFlightNumber(text: string): string | null {
  const m = text.replace(/\s+/g, "").toUpperCase().match(/^([A-Z]{3}|[A-Z0-9]{2})(\d{1,4}[A-Z]?)$/);
  return m && /[A-Z]/.test(m[1]) ? `${m[1]} ${m[2]}` : null;
}

/** The legs a flight number flies on a date (its local departure date). */
export async function lookupFlight(number: string, date: string): Promise<FlightOption[]> {
  const t0 = performance.now();
  const res = await fetch(`/api/flight?${new URLSearchParams({ number, date })}`);
  if (!res.ok) {
    let msg = `Flight lookup returned ${res.status}.`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
    } catch {
      /* keep the generic message */
    }
    throw new Error(msg);
  }
  const data = await res.json();
  logTimings(`Flight lookup ${number}`, performance.now() - t0, data.timings);
  return data.legs;
}

export async function assessTrip(body: {
  origin: string;
  destination: string;
  date: string;
  time?: string;
  mode: Mode;
  legs?: FlightLeg[];
}, onUpdate?: (res: AssessResponse) => void): Promise<AssessResponse> {
  const t0 = performance.now();
  // progressive results: one JSON answer per line, the quick rules-only one first (ai_pending),
  // then the AI's. onUpdate gets each; the promise resolves with the last.
  const res = await fetch("/api/assess/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // empty time = check the whole day; no legs = estimate the flight. display_tz: the date and time
    // are on the viewer's clock, and every time in the answer comes back on it
    body: JSON.stringify({ ...body, time: body.time || null, legs: body.legs?.length ? body.legs : null,
      display_tz: viewerTz() }),
  });
  if (!res.ok) {
    let msg = `The assessment service returned ${res.status}.`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail)) msg = data.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      if (res.status >= 500) msg = "Can't reach the backend. Start it with: uvicorn app.main:app --port 8000";
    }
    throw new Error(msg);
  }
  let last: AssessResponse | null = null;
  const take = (line: string) => {
    if (!line.trim()) return;
    const msg = JSON.parse(line);
    if (msg.error) { // the AI's answer failed: keep the first one
      console.warn(`[Trip Check] AI summary didn't arrive: ${msg.error}`);
      if (last) last = { ...last, ai_pending: false };
      return;
    }
    last = msg as AssessResponse;
    const what = last.ai_pending ? "first answer (rules only)" : "AI answer";
    logTimings(`Check ${body.origin} → ${body.destination}, ${what}`, performance.now() - t0, last.timings);
    onUpdate?.(last);
  };
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    buf += decoder.decode(value, { stream: !done });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    lines.forEach(take);
    if (done) break;
  }
  take(buf);
  if (!last) throw new Error("The assessment service sent no answer.");
  return last;
}
