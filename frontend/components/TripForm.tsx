"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { viewerTz, type FlightLeg, type Mode } from "@/lib/types";
import { FlightLegs } from "./FlightLegs";
import { PlaceInput } from "./PlaceInput";

export interface TripInput {
  origin: string;
  destination: string;
  date: string;
  time: string; // "HH:MM" on the viewer's clock, or "" for the whole day
  mode: Mode;
  legs: FlightLeg[]; // the traveller's actual flights, when given (Air only)
}

// The form's date and time are on the viewer's clock (the backend reads them in the browser's zone).
// An example's `time` and `offset` (days from today) are the origin's local time, converted when
// clicked; `inHours` instead departs a few hours from now (live FAA data counts).
const EXAMPLES: Array<Omit<TripInput, "date" | "legs"> & { label: string; tz: string; offset?: number; inHours?: number }> = [
  { label: "New York to San Francisco, tomorrow at 8 AM ET", origin: "New York, NY", destination: "San Francisco, CA", time: "08:00", mode: "fly", tz: "America/New_York", offset: 1 },
  { label: "Chicago to Columbus, in 3 hours", origin: "Chicago, IL", destination: "Columbus, OH", time: "", mode: "fly", tz: "America/Chicago", inHours: 3 },
  { label: "Denton to Austin by car, tomorrow at 5 PM CT", origin: "Denton, TX", destination: "Austin, TX", time: "17:00", mode: "drive", tz: "America/Chicago", offset: 1 },
];

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Date `offsetDays` from today in the viewer's browser (only a default before a place is picked). */
export function isoDate(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return ymd(d);
}

/** Calendar date and 24 h time of instant t, as a clock in time zone tz shows it. */
function clockIn(tz: string, t: Date) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(t).map((x) => [x.type, x.value]),
  );
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}

/** The date `offsetDays` from today in time zone tz. */
function dateIn(tz: string, offsetDays: number): string {
  const [y, m, d] = clockIn(tz, new Date()).date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + offsetDays)).toISOString().slice(0, 10);
}

/** Date and time `hours` from now on the viewer's clock, rounded up to a quarter hour. */
function inHours(hours: number): { date: string; time: string } {
  return clockIn(viewerTz(), new Date(Math.ceil((Date.now() + hours * 3600_000) / 900_000) * 900_000));
}

/** The instant a clock in tz shows date ("YYYY-MM-DD") and time ("HH:MM"). */
function instantIn(tz: string, date: string, time: string): Date {
  const want = Date.parse(`${date}T${time}:00Z`);
  let t = want;
  for (let i = 0; i < 2; i++) { // twice: the offset can change in between (DST)
    const c = clockIn(tz, new Date(t));
    t += want - Date.parse(`${c.date}T${c.time}:00Z`);
  }
  return new Date(t);
}

/** "6:12 AM CST": the current time in tz. */
function nowIn(tz: string): string {
  return new Date().toLocaleTimeString("en-US", { timeZone: tz, hour: "numeric", minute: "2-digit", timeZoneName: "short" });
}

/** "CDT": the viewer's zone, short. */
function zoneName(tz: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" }).formatToParts(new Date())
    .find((p) => p.type === "timeZoneName")?.value ?? tz;
}

/** Same clock now in both zones? */
function sameClock(a: string, b: string): boolean {
  const now = new Date();
  const x = clockIn(a, now), y = clockIn(b, now);
  return x.date === y.date && x.time === y.time;
}

export function TripForm({
  value,
  onChange,
  onSubmit,
  busy,
}: {
  value: TripInput;
  onChange: (v: TripInput) => void;
  onSubmit: (v: TripInput) => void;
  busy: boolean;
}) {
  // A place counts only once it has been picked from the suggestion list.
  type PlaceKey = "origin" | "destination";
  const [confirmed, setConfirmed] = useState({ origin: false, destination: false });
  const [errors, setErrors] = useState<Record<PlaceKey, string | null>>({ origin: null, destination: null });
  const [timeError, setTimeError] = useState<string | null>(null);
  const [originTz, setOriginTz] = useState<string | null>(null); // the departure time is local to here
  const refs = { origin: useRef<HTMLInputElement>(null), destination: useRef<HTMLInputElement>(null) };
  // the browser's zone, read after mounting (the server render doesn't know it)
  const [tz, setTz] = useState<string | null>(null);
  useEffect(() => setTz(viewerTz()), []);

  // a new date or time means a different departure than the chosen flights
  const setDate = (e: React.ChangeEvent<HTMLInputElement>) => onChange({ ...value, date: e.target.value, legs: [] });

  // chosen flights belong to one route, so changing the route drops them
  const typed = (k: PlaceKey) => (text: string) => {
    onChange({ ...value, [k]: text, legs: [] });
    setConfirmed((c) => ({ ...c, [k]: false }));
    setErrors((e) => ({ ...e, [k]: null }));
    if (k === "origin") setOriginTz(null);
  };
  const picked = (k: PlaceKey) => (v: string, tz?: string | null) => {
    onChange({ ...value, [k]: v, legs: value[k] === v ? value.legs : [] });
    setConfirmed((c) => ({ ...c, [k]: true }));
    setErrors((e) => ({ ...e, [k]: null }));
    if (k === "origin") setOriginTz(tz ?? null);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const missing = (["origin", "destination"] as const).filter((k) => !confirmed[k]);
    if (missing.length) {
      setErrors({
        origin: confirmed.origin ? null : "Choose a city or airport from the list.",
        destination: confirmed.destination ? null : "Choose a city or airport from the list.",
      });
      refs[missing[0]].current?.focus();
      return;
    }
    if (value.origin.trim().toLowerCase() === value.destination.trim().toLowerCase()) {
      setErrors({ origin: null, destination: "Pick a destination different from where you're leaving." });
      refs.destination.current?.focus();
      return;
    }
    if (!value.time) {
      // the risks shown are for the hours you're travelling, so the time is needed
      setTimeError("Enter when you'll leave.");
      document.getElementById("dep-time")?.focus();
      return;
    }
    onSubmit(value);
  };

  return (
    <div className="trip-form-wrap">
      <form className="trip-form" onSubmit={submit} aria-label="Trip details" noValidate>
        <PlaceInput
          ref={refs.origin}
          label="From"
          value={value.origin}
          confirmed={confirmed.origin}
          error={errors.origin}
          onType={typed("origin")}
          onPick={picked("origin")}
        />
        <PlaceInput
          ref={refs.destination}
          label="To"
          value={value.destination}
          confirmed={confirmed.destination}
          error={errors.destination}
          onType={typed("destination")}
          onPick={picked("destination")}
        />
        <label className="field field-date">
          <span>Travel date</span>
          {/* the viewer's today: the date and time are on their clock */}
          <input required type="date" min={isoDate(0)} value={value.date}
            onChange={setDate} />
        </label>
        <div className="field field-time">
          <label htmlFor="dep-time">Leave at</label>
          <input id="dep-time" type="time" step={60} value={value.time} onChange={(e) => {
              onChange({ ...value, time: e.target.value, legs: [] });
              setTimeError(null);
            }}
            aria-invalid={!!timeError} aria-describedby="dep-time-hint" />
          <span className={timeError ? "field-error" : "field-hint"} id="dep-time-hint">
            {timeError ??
              (!tz
                ? "When you set off, your time"
                : `Your time (${zoneName(tz)}) · now ${nowIn(tz)}` +
                (originTz && confirmed.origin && !sameClock(originTz, tz)
                  ? ` · ${value.origin.split(",")[0]} now ${nowIn(originTz)}`
                  : ""))}
          </span>
        </div>
        <label className="field field-mode">
          <span>Travelling by</span>
          <select value={value.mode} onChange={(e) => onChange({ ...value, mode: e.target.value as Mode, legs: [] })}>
            <option value="fly">Air</option>
            <option value="drive">Car</option>
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Checking…" : "Check trip"}
        </button>
      </form>
      {value.mode === "fly" && (
        <FlightLegs
          origin={value.origin}
          destination={value.destination}
          date={value.date}
          time={value.time}
          ready={confirmed.origin && confirmed.destination && !!value.date && !!value.time}
          legs={value.legs}
          checking={busy}
          onChange={(legs) => onChange({ ...value, legs })}
          onDone={(legs) => onSubmit({ ...value, legs })} // the page then fills in when to leave
        />
      )}
      <p className="examples">
        Try{" "}
        {EXAMPLES.map((ex, i) => (
          <span key={ex.label}>
            <button
              type="button"
              className="link"
              disabled={busy}
              onClick={() => {
                const when = ex.inHours
                  ? inHours(ex.inHours)
                  : clockIn(viewerTz(), instantIn(ex.tz, dateIn(ex.tz, ex.offset ?? 0), ex.time));
                const v = { origin: ex.origin, destination: ex.destination, mode: ex.mode, legs: [], ...when };
                onChange(v);
                setConfirmed({ origin: true, destination: true });
                setOriginTz(ex.tz);
                setErrors({ origin: null, destination: null });
                setTimeError(null);
                onSubmit(v);
              }}
            >
              {ex.label}
            </button>
            {i < EXAMPLES.length - 1 ? ", " : ""}
          </span>
        ))}
      </p>
    </div>
  );
}
