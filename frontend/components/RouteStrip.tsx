import { Fragment } from "react";
import type { AssessResponse, Segment } from "@/lib/types";

const STATUS: Record<AssessResponse["risk_level"], string> = {
  low: "Clear to go",
  medium: "Watch closely",
  high: "Disruption likely",
};

function formatDate(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function whenText(days: number) {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

/** 85 -> "1 h 25 min" */
function duration(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h && m ? `${h} h ${m} min` : h ? `${h} h` : `${m} min`;
}

function Plane() {
  return (
    <svg className="plane" viewBox="0 0 24 24" aria-hidden="true">
      <path
        transform="rotate(90 12 12)"
        d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"
      />
    </svg>
  );
}

function Car() {
  return (
    <svg className="plane" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z" />
    </svg>
  );
}

function Hop({ top, icon, bottom, label }: { top: string; icon: React.ReactNode; bottom: string; label: string }) {
  return (
    <li className="hop" aria-label={label}>
      <span className="hop-top" aria-hidden="true">{top}</span>
      <span className="hop-mid">{icon}</span>
      <span className="hop-bot" aria-hidden="true">{bottom}</span>
    </li>
  );
}

function Stop({ code, note }: { code: string; note: string }) {
  return (
    <li className="stop" aria-label={`${code}: ${note}`}>
      <span className="hop-top stop-code" aria-hidden="true">{code}</span>
      <span className="hop-mid">
        <span className="stop-dot" />
      </span>
      <span className="hop-bot" aria-hidden="true">{note}</span>
    </li>
  );
}

/** The line between the ends, drawn per step: the drive to the airport, each flight (plane, flight
 * number, time in the air) with a stop for each layover, and the drive from the airport. */
function FlightPath({ segments, driveTo, driveFrom }: { segments: Segment[]; driveTo?: number | null; driveFrom?: number | null }) {
  const first = segments[0];
  const last = segments[segments.length - 1];
  return (
    <ol className="path" aria-label="Your journey">
      {driveTo != null && (
        <>
          <Hop top="Car" icon={<Car />} bottom={duration(driveTo)} label={`Drive to ${first.dep_iata}, ${duration(driveTo)}`} />
          <Stop code={first.dep_iata} note="1 h check-in" />
        </>
      )}
      {segments.map((s, i) => (
        <Fragment key={i}>
          <Hop
            top={s.flight ?? "Estimated flight"}
            icon={<Plane />}
            bottom={`${s.estimated ? "about " : ""}${duration(s.minutes)}`}
            label={`${s.flight ?? "Estimated flight"} from ${s.dep_iata} to ${s.arr_iata}, ${s.estimated ? "about " : ""}${duration(s.minutes)}`}
          />
          {s.layover_min != null && <Stop code={s.arr_iata} note={`${duration(s.layover_min)} layover`} />}
        </Fragment>
      ))}
      {driveFrom != null && (
        <>
          <Stop code={last.arr_iata} note="30 min bags" />
          <Hop top="Car" icon={<Car />} bottom={duration(driveFrom)} label={`Drive from ${last.arr_iata}, ${duration(driveFrom)}`} />
        </>
      )}
    </ol>
  );
}

/** The hero: the route as two endpoints joined by a line, with the trip's status. */
/** Opens the flight picker's "Enter a flight yourself" form and puts the cursor in its flight number box. */
function openFlightEntry() {
  const button = document.getElementById("enter-flight");
  if (!button) {  // the picker isn't showing that button right now: just go to the flights
    document.getElementById("flights-h")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (button.getAttribute("aria-expanded") !== "true") button.click();
  setTimeout(() => {  // after the form has rendered
    const input = document.querySelector<HTMLInputElement>(".manual-leg input");
    (input ?? button).scrollIntoView({ behavior: "smooth", block: "center" });
    input?.focus({ preventScroll: true });
  }, 0);
}

export function RouteStrip({ data, pending }: { data?: AssessResponse; pending?: { origin: string; destination: string } }) {
  if (!data) {
    return (
      <section className="route route-pending" aria-live="polite" aria-busy="true">
        <div className="route-ends">
          <div className="end">
            <span className="code code-soft">{pending?.origin ?? ""}</span>
          </div>
          <svg className="route-line" viewBox="0 0 400 24" preserveAspectRatio="none" aria-hidden="true">
            <line x1="4" y1="12" x2="396" y2="12" className="drawing" />
          </svg>
          <div className="end end-right">
            <span className="code code-soft">{pending?.destination ?? ""}</span>
          </div>
        </div>
        <p className="route-status-text">Checking weather, airport status and news along your route…</p>
      </section>
    );
  }

  const fly = data.mode === "fly";
  const oCode = fly ? data.origin_airports[0]?.iata : data.origin.name.split(",")[0];
  const dCode = fly ? data.destination_airports[0]?.iata : data.destination.name.split(",")[0];
  const oAlt = fly ? data.origin_airports.slice(1).map((a) => a.iata) : [];
  const dAlt = fly ? data.destination_airports.slice(1).map((a) => a.iata) : [];
  // with a drive to/from the airport, that end is the city and the airport is a stop on the line
  const oCity = !fly || data.timing?.drive_to_min != null;
  const dCity = !fly || data.timing?.drive_from_min != null;

  return (
    <section className={`route route-${data.risk_level}`} aria-label="Trip status">
      <div className="route-ends">
        <div className="end">
          <span className={`code ${oCity ? "code-stop" : ""}`}>{oCity ? data.origin.name.split(",")[0] : oCode}</span>
          <span className="place">{data.origin.name}</span>
          {oAlt.length > 0 && <span className="alt">Also checked {oAlt.join(" and ")}</span>}
        </div>
        {fly && data.timing?.segments.length ? (
          <FlightPath segments={data.timing.segments} driveTo={data.timing.drive_to_min} driveFrom={data.timing.drive_from_min} />
        ) : !fly && data.timing ? (
          // by car: one stretch with the whole trip's time (overnight stops included)
          <ol className="path" aria-label="Your journey">
            <Hop
              top="Car"
              icon={<Car />}
              bottom={`about ${duration(Math.round(data.timing.duration_hours * 60))}${
                data.timing.overnight_stops
                  ? ` incl. ${data.timing.overnight_stops} overnight stop${data.timing.overnight_stops > 1 ? "s" : ""}`
                  : ""
              }`}
              label={`Drive, about ${duration(Math.round(data.timing.duration_hours * 60))}`}
            />
          </ol>
        ) : (
          <svg className="route-line" viewBox="0 0 400 24" preserveAspectRatio="none" aria-hidden="true">
            <line x1="4" y1="12" x2="396" y2="12" />
            <circle cx="4" cy="12" r="4" />
            <circle cx="396" cy="12" r="4" />
          </svg>
        )}
        <div className="end end-right">
          <span className={`code ${dCity ? "code-stop" : ""}`}>{dCity ? data.destination.name.split(",")[0] : dCode}</span>
          <span className="place">{data.destination.name}</span>
          {dAlt.length > 0 && <span className="alt">Also checked {dAlt.join(" and ")}</span>}
        </div>
      </div>

      <div className="route-status">
        <p className="status-word">{STATUS[data.risk_level]}</p>
        <p className="status-meta">
          {fly ? "Flying" : "Driving"} {formatDate(data.request.date)} ({whenText(data.days_out)})
          {data.timing && (
            <>
              , {data.timing.departure} → {data.timing.from_flights ? "" : "about "}{data.timing.est_arrival}
              {data.timing.overnight_stops > 0 &&
                ` with ${data.timing.overnight_stops} overnight stop${data.timing.overnight_stops > 1 ? "s" : ""}`}
            </>
          )}
          . Confidence: {data.confidence}.
        </p>
        {data.timing?.leave_by && (
          <ol className="route-legs">
            <li>
              Leave {data.origin.name.split(",")[0]} by <strong>{data.timing.leave_by}</strong> to be at the airport by{" "}
              {data.timing.at_airport_by} (1 hour for bag drop and security)
            </li>
            {data.timing.drive_to_airport && <li>{data.timing.drive_to_airport}</li>}
            {data.timing.from_flights &&
              data.timing.legs.map((l, i) => {
                const wait = data.timing?.segments[i];
                return (
                  <Fragment key={l}>
                    <li>{l}</li>
                    {wait?.layover_min != null && (
                      <li>
                        Layover at {wait.arr_iata}: {duration(wait.layover_min)}
                      </li>
                    )}
                  </Fragment>
                );
              })}
            {data.timing.est_flight && <li>Estimated flight · {data.timing.est_flight} (nonstop, assumed)</li>}
            <li>Baggage claim: about 30 min, out of the airport by {data.timing.out_of_airport}</li>
            {data.timing.drive_from_airport && (
              <>
                <li>{data.timing.drive_from_airport}</li>
                <li>
                  At {data.destination.name.split(",")[0]} {data.timing.from_flights ? "around" : "about"} {data.timing.door_arrival}
                </li>
              </>
            )}
          </ol>
        )}
        {data.timing?.est_flight && (
          <p className="assumed" role="note">
            <strong>No flight chosen, so the flight times are an assumption.</strong> You chose to travel by air, so
            this assumes a nonstop flight leaving as soon as you&apos;re through the airport. Private jets and
            helicopters aren&apos;t fully covered: the times assume an airline-style trip with check-in, baggage claim
            and a main airport. It&apos;s timed at about {data.timing.cruise_mph} mph cruising
            speed plus {data.timing.overhead_min} min for taxi, climb and landing: {data.timing.moving_hours} hours for{" "}
            {data.timing.flight_miles} miles, about{" "}
            {Math.round((data.timing.flight_miles ?? 0) / data.timing.moving_hours)} mph overall. For real times,
            enter your flight number at{" "}
            <button type="button" className="link" onClick={openFlightEntry}>Enter a flight yourself</button>.
          </p>
        )}
        {data.timing?.better_departure && <p className="better-time">{data.timing.better_departure}</p>}
      </div>
      <h1 className="headline">{data.assessment.headline}</h1>
      <p className="summary">{data.assessment.summary}</p>
      {data.ai_pending && (
        <p className="ai-pending" role="status">
          <span className="spinner" aria-hidden="true" />
          Summary from the rules for now. The AI is reading the evidence and news; its summary replaces this when ready.
        </p>
      )}
    </section>
  );
}
