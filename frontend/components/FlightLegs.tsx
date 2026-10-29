"use client";

import { useEffect, useRef, useState } from "react";
import { findFlights, lookupFlight, normalizeFlightNumber, type FlightLeg, type FlightOption } from "@/lib/types";

const MIN_CONNECTION_MIN = 40; // search the next flight from this long after landing

/** "2026-11-02T09:00" -> "9:00 AM" (read as written: each airport's own clock). */
function clock(local: string): string {
  const [h, m] = local.slice(11, 16).split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

function day(local: string): string {
  const [y, mo, d] = local.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, d)).toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });
}

/** "" when local isn't a full "YYYY-MM-DDTHH:MM" (e.g. a flight entered without its arrival time). */
function addMinutes(local: string, minutes: number): string {
  const [y, mo, d] = local.slice(0, 10).split("-").map(Number);
  const [h, m] = local.slice(11, 16).split(":").map(Number);
  const t = new Date(Date.UTC(y, mo - 1, d, h, m + minutes));
  return Number.isNaN(t.getTime()) ? "" : t.toISOString().slice(0, 16);
}

/**
 * The traveller's actual flights: found by lookup (AeroDataBox) or typed in. Each new flight
 * starts where the last one landed, so a connection is just "find flights to CLT", then
 * "find flights to the destination" from there. The first search runs by itself once the
 * trip is filled in. A trip can have several flights, so the check runs only on Done.
 */
export function FlightLegs({
  origin,
  destination,
  date,
  time,
  ready,
  legs,
  checking,
  onChange,
  onDone,
}: {
  origin: string;
  destination: string;
  date: string;
  time: string;
  ready: boolean; // From/To picked and a date + leave time set
  legs: FlightLeg[];
  checking: boolean; // the trip check is running
  onChange: (legs: FlightLeg[]) => void;
  onDone: (legs: FlightLeg[]) => void;
}) {
  const [via, setVia] = useState("");
  const [options, setOptions] = useState<FlightOption[] | null>(null);
  const [earliest, setEarliest] = useState<Record<string, string>>({}); // per airport: first departure you can make
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const [destAps, setDestAps] = useState<string[]>([]); // the destination's airports, from the searches
  const latest = useRef(0); // ignore answers to searches that have been superseded
  const last = legs[legs.length - 1];
  const reached = !!last?.arr_iata && destAps.includes(last.arr_iata); // nothing more to search for
  // a flight entered without where/when it lands can't be searched on from; the next one is entered by hand
  // (its departure airport tells the check where the previous flight landed)
  const lastOpen = !last || (!!last.arr_iata && !!last.arr_local);
  const lastArr = last?.arr_local ?? "";
  const from = last ? last.arr_iata ?? "" : origin;
  const firstAirport = Object.keys(earliest)[0];
  const manualFrom = last ? from : from.length === 3 ? from.toUpperCase() : firstAirport ?? "";
  const manualDep = last
    ? addMinutes(lastArr || last.dep_local, lastArr ? 60 : 120) // unknown landing: a guess to edit
    : earliest[manualFrom] ?? `${date}T${time}`;

  async function search(to: string) {
    // from home: the backend adds the drive and an hour at the airport; a connection starts after landing
    const start = last ? addMinutes(lastArr, MIN_CONNECTION_MIN) : `${date}T${time}`;
    const id = ++latest.current;
    setBusy(true);
    setError(null);
    setOptions(null);
    setTarget(to);
    try {
      const res = await findFlights(from, to, start.slice(0, 10), start.slice(11, 16), !last);
      if (id !== latest.current) return;
      setOptions(res.results);
      setEarliest(res.earliest);
      if (to === destination) setDestAps(res.destination_airports);
    } catch (e) {
      if (id === latest.current) setError(e instanceof Error ? e.message : "Flight lookup failed.");
    } finally {
      if (id === latest.current) setBusy(false);
    }
  }

  useEffect(() => setDestAps([]), [destination]);

  // list the flights by itself once the trip is filled in (debounced: each search uses free quota)
  useEffect(() => {
    // the trip changed: the old list (and any search still running) no longer applies
    latest.current++;
    setOptions(null);
    setEarliest({});
    setError(null);
    setBusy(false);
    if (!ready || legs.length) return;
    const t = setTimeout(() => search(destination), 900);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, origin, destination, date, time, legs.length]);

  const [added, setAdded] = useState<string | null>(null); // last flight the form added, for the note

  // keepOpen: added from the form by its number, so the form stays for the next flight
  function add(leg: FlightLeg, keepOpen = false) {
    onChange([...legs, leg]);
    setOptions(null);
    setVia("");
    setManual(keepOpen);
    setAdded(keepOpen ? `${leg.flight} added (${leg.dep_iata} → ${leg.arr_iata}).` : null);
  }

  // Done in the form: a flight still filled in there counts too
  function done(pending: FlightLeg | null) {
    const all = pending ? [...legs, pending] : legs;
    if (pending) onChange(all);
    setManual(false);
    setAdded(null);
    onDone(all);
  }

  return (
    <section className="flights" aria-labelledby="flights-h">
      <h2 id="flights-h">Your flights</h2>
      <p className="quiet">
        Pick your flight and the check uses its real times, works out when to leave, and covers any connection
        airport. Without one, the flight is estimated.
      </p>

      {legs.length > 0 && (
        <ol className="legs">
          {legs.map((l, i) => (
            <li key={i}>
              <span className="leg-no">{l.flight || "Flight"}</span>
              <span>
                {l.dep_iata} {clock(l.dep_local)} →{" "}
                {l.arr_iata ?? legs[i + 1]?.dep_iata ?? "airport nearest " + destination.split(",")[0]}{" "}
                {l.arr_local ? clock(l.arr_local) : "(arrival estimated)"}
              </span>
              <span className="quiet">{day(l.dep_local)}</span>
              {i === legs.length - 1 && (
                <button type="button" className="link" onClick={() => onChange(legs.slice(0, -1))}>
                  Remove
                </button>
              )}
            </li>
          ))}
        </ol>
      )}

      {!ready ? (
        <p className="quiet">Pick From, To, date and the time you leave, and the flights you can make are listed here.</p>
      ) : reached ? (
        <p className="quiet">
          Your flights reach {destination.split(",")[0]} ({last.arr_iata}). To change the last one, remove it.
        </p>
      ) : !lastOpen ? (
        <div className="leg-actions">
          <span className="quiet">Connecting? Add the next flight. Its From airport is where this one lands.</span>
          <button type="button" className="secondary" onClick={() => setManual((m) => !m)}>
            {manual ? "Cancel" : "Add the next flight"}
          </button>
        </div>
      ) : (
        <div className="leg-actions">
          <button type="button" className="secondary" disabled={busy} onClick={() => search(destination)}>
            {busy && target === destination ? "Searching…" : `Find flights ${from.split(",")[0]} → ${destination.split(",")[0]}`}
          </button>
          <span className="leg-via">
            <span>or connect via</span>
            <input
              aria-label="Connection airport code"
              value={via}
              maxLength={3}
              placeholder="CLT"
              onChange={(e) => setVia(e.target.value.replace(/[^a-z]/gi, "").toUpperCase())}
            />
            <button type="button" className="secondary" disabled={busy || via.length !== 3} onClick={() => search(via)}>
              {busy && target === via ? "Searching…" : "Find flights"}
            </button>
          </span>
          <button type="button" id="enter-flight" className="link" aria-expanded={manual}
            onClick={() => setManual((m) => !m)}>
            {manual ? "Cancel" : "Enter a flight yourself"}
          </button>
        </div>
      )}

      {legs.length > 0 && !manual && (
        <p className="legs-done">
          <button type="button" className="primary" disabled={checking} onClick={() => done(null)}>
            {checking ? "Checking…" : "Done, check these flights"}
          </button>
          {!reached && <span className="quiet">Add every flight of the trip first.</span>}
        </p>
      )}

      {manual && (
        // above the list, so it's in reach without scrolling past every flight.
        // defaults: the connection airport, or the nearest airport and the first departure you can make there;
        // keyed so the form resets when those change
        <ManualLeg key={`${manualFrom}|${manualDep}`} from={manualFrom} defaultDep={manualDep} added={added}
          checking={checking} onFound={(l) => add(l, true)} onDone={done} />
      )}

      {error && <p className="field-error">{error}</p>}

      {options && (
        <div className="leg-options">
          {options.length === 0 ? (
            <p className="quiet">
              No scheduled flights {from} → {target.split(",")[0]} in the 12 hours after that time. Try a connection, or
              enter your flight yourself.
            </p>
          ) : (
            <>
              <p className="quiet">
                {last
                  ? `Leaving ${from} after ${clock(lastArr)} plus ${MIN_CONNECTION_MIN} min to connect.`
                  : `Leaving at ${clock(`${date}T${time}`)} your time, you can make flights ${
                      Object.entries(earliest).map(([ap, t]) => `from ${ap} after ${clock(t)}`).join(" or ") || "after then"
                    } (the drive + 1 hour at the airport).`}{" "}
                Flight times are each airport&apos;s local time, as on a booking. Pick yours:
              </p>
              <ul>
                {options.map((o) => (
                  <li key={o.flight + o.dep_local}>
                    <button type="button" className="leg-option" onClick={() => add(o)}>
                      <span className="leg-no">{o.flight}</span>
                      <span>
                        {o.dep_iata} {clock(o.dep_local)} → {o.arr_iata} {clock(o.arr_local)}
                      </span>
                      <span className="quiet">{o.airline}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function ManualLeg({ from, defaultDep, added, checking, onFound, onDone }: {
  from: string;
  defaultDep: string;
  added: string | null; // the flight the form added last
  checking: boolean;
  onFound: (l: FlightLeg) => void; // a flight number found in the schedule: add it
  onDone: (pending: FlightLeg | null) => void; // with a flight filled in by hand, if any
}) {
  const [leg, setLeg] = useState<FlightLeg>({ flight: "", dep_iata: from, arr_iata: "", dep_local: defaultDep, arr_local: "" });
  const [err, setErr] = useState<string | null>(null);
  const [found, setFound] = useState<string | null>(null); // what the lookup filled in
  const [looking, setLooking] = useState(false);
  const code = (v: string) => v.replace(/[^a-z]/gi, "").toUpperCase().slice(0, 3);
  const [touched, setTouched] = useState(false); // something typed besides the defaults
  const doneAfter = useRef(false);
  const set = (k: keyof FlightLeg, v: string) => {
    setTouched(true);
    setLeg((l) => ({ ...l, [k]: v }));
  };

  // on leaving the flight box: tidy the number ("wn3872" -> "WN 3872") and fill in the rest from the schedule
  async function lookUp() {
    const text = (leg.flight ?? "").trim();
    setFound(null);
    if (!text) return;
    const number = normalizeFlightNumber(text);
    if (!number) return setErr("That doesn't look like a flight number. Use one like WN 3872.");
    setErr(null);
    set("flight", number);
    const day = (leg.dep_local || defaultDep).slice(0, 10);
    setLooking(true);
    try {
      const options = await lookupFlight(number, day);
      // a flight number can fly several legs a day: the one from this airport, else the first
      const hit = options.find((o) => o.dep_iata === leg.dep_iata) ?? options[0];
      if (!hit) return setFound(`${number} isn't in the schedule on ${day}. Fill in the rest yourself.`);
      // found: it goes straight into the list and the form clears for the next flight
      // (or, if Done was clicked meanwhile, the trip is checked with it)
      const l = { flight: number, dep_iata: hit.dep_iata, arr_iata: hit.arr_iata, dep_local: hit.dep_local, arr_local: hit.arr_local };
      if (doneAfter.current) onDone(l);
      else onFound(l);
    } catch (e) {
      setFound(`${e instanceof Error ? e.message : "Lookup failed."} Fill in the rest yourself.`);
    } finally {
      doneAfter.current = false;
      setLooking(false);
    }
  }

  // Done: a flight filled in by hand is added first (only From and Departs are needed; a blank
  // To / Arrives is filled in by the check). Untouched defaults aren't a flight.
  function submit() {
    if (looking) {
      doneAfter.current = true; // clicked while the number is being looked up: finish when it's found
      return;
    }
    if (!touched) return onDone(null);
    const to = leg.arr_iata || "";
    if (leg.dep_iata.length !== 3 || (to && to.length !== 3)) return setErr("Use 3-letter airport codes, e.g. LAX.");
    if (!leg.dep_local) return setErr("Enter when the flight departs.");
    onDone({ ...leg, flight: normalizeFlightNumber(leg.flight ?? "") ?? (leg.flight?.trim() || null),
             arr_iata: to || null, arr_local: leg.arr_local || null });
  }

  return (
    <div className="manual-leg">
      <label className="field">
        <span>Flight (optional)</span>
        <input value={leg.flight ?? ""} placeholder="WN 3872" maxLength={12} autoComplete="off"
          onChange={(e) => set("flight", e.target.value.toUpperCase())} onBlur={lookUp}
          onKeyDown={(e) => e.key === "Enter" && lookUp()} />
      </label>
      <label className="field">
        <span>From</span>
        <input value={leg.dep_iata} placeholder="LAX" onChange={(e) => set("dep_iata", code(e.target.value))} />
      </label>
      <label className="field">
        <span>Departs (local)</span>
        <input type="datetime-local" value={leg.dep_local} onChange={(e) => set("dep_local", e.target.value)} />
      </label>
      <label className="field">
        <span>To (optional)</span>
        <input value={leg.arr_iata ?? ""} placeholder="Nearest" onChange={(e) => set("arr_iata", code(e.target.value))} />
      </label>
      <label className="field">
        <span>Arrives (optional)</span>
        <input type="datetime-local" value={leg.arr_local ?? ""} onChange={(e) => set("arr_local", e.target.value)} />
      </label>
      <p className="quiet manual-note" aria-live="polite">
        {looking
          ? "Looking up the flight…"
          : found ??
            (added
              ? `${added} Type the next flight number, or click Done if that's all.`
              : "Type the flight number and it's added from the schedule. Otherwise only From and Departs are needed: a blank To means the airport nearest your destination, and a blank Arrives is estimated from the distance.")}
      </p>
      <button type="button" className="primary" disabled={checking} onClick={submit}>
        {checking ? "Checking…" : "Done, check these flights"}
      </button>
      {err && <p className="field-error">{err}</p>}
    </div>
  );
}
