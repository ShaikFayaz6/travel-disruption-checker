import type { AssessResponse, Signal } from "@/lib/types";

const SIDE_LABEL: Record<Signal["side"], string> = {
  origin: "At departure",
  route: "Along the way",
  destination: "At arrival",
  national: "In the news",
};
const SIDES: Signal["side"][] = ["origin", "route", "destination", "national"];
const SEV = ["Context", "Low risk", "Moderate risk", "High risk"];

function fmtTime(v?: string | null) {
  if (!v) return null;
  // GDELT uses 20261026T120000Z
  const m = v.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  const d = new Date(m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z` : v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * "2026-11-02T06:00:00-08:00" -> {day: "Mon Nov 2", time: "4:00 AM", zone: "CST"}: a stamp with an
 * offset is shown on the viewer's clock; one without is read as written (the place's own clock).
 */
function parseStamp(v: string) {
  const m = v.trim().match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/);
  if (!m) return null;
  const [, y, mo, d, h, mi, off] = m;
  if (off) {
    const t = new Date(v.trim());
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
    }).formatToParts(t).map((x) => [x.type, x.value]));
    return { day: `${p.weekday} ${p.month} ${p.day}`, time: `${p.hour}:${p.minute} ${p.dayPeriod}`, zone: p.timeZoneName };
  }
  const date = new Date(Number(y), Number(mo) - 1, Number(d));
  const hour = Number(h);
  return {
    day: `${DAYS[date.getDay()]} ${MONTHS[date.getMonth()]} ${Number(d)}`,
    time: `${hour % 12 || 12}:${mi} ${hour < 12 ? "AM" : "PM"}`,
    zone: "local time",
  };
}

/** Turns "ISO → ISO" ranges into "Mon Nov 2, 6:00 AM – 6:00 PM CST"; other text passes through. */
function fmtCovers(v: string): string {
  const parts = v.split("→").map((x) => x.trim());
  if (parts.length !== 2) return v;
  const [a, b] = parts.map(parseStamp);
  if (!a || !b) return v;
  return a.day === b.day
    ? `${a.day}, ${a.time} – ${b.time} ${b.zone}`
    : `${a.day}, ${a.time} ${a.zone} – ${b.day}, ${b.time} ${b.zone}`;
}

function Item({ s, highlighted }: { s: Signal; highlighted: boolean }) {
  const observed = fmtTime(s.observed_at);
  return (
    <li id={`ev-${s.id}`} className={`ev sev${s.severity} ${highlighted ? "flash" : ""}`}>
      <div className="ev-head">
        <span className="ev-sev">{SEV[s.severity]}</span>
        <h4>{s.title}</h4>
      </div>
      <p className="ev-detail">{s.detail}</p>
      <p className="ev-meta">
        {s.url ? (
          <a href={s.url} target="_blank" rel="noreferrer">
            {s.source}
          </a>
        ) : (
          s.source
        )}
        {", "}
        {s.location}
        {observed && <>, data from {observed}</>}
        {s.valid_for && s.valid_for !== "live" && <>. Covers: {fmtCovers(s.valid_for)}</>}
        {s.valid_for === "live" && <>. Live status at time of check</>}
      </p>
    </li>
  );
}

/** "No significant weather: Overcast" -> "Overcast, no significant weather" (reads better in a list). */
function shortTitle(s: Signal): string {
  const m = s.title.match(/^No significant weather: (.+)$/);
  return m ? `${m[1]}, no significant weather` : s.title;
}

/**
 * The checks that didn't raise the rating, as a timeline in trip order: each place with the hours
 * you're there, then one line per check. Signals are matched to places by location
 * ("DFW (Denton, TX)" for weather, "DFW" for airport feeds); anything unmatched goes last.
 */
function ContextTimeline({ data, items, highlight }: { data: AssessResponse; items: Signal[]; highlight: string | null }) {
  const places = data.timing ? Object.entries(data.timing.windows) : [];
  const placeOf = (s: Signal) =>
    places.find(([p]) => p === s.location || p.startsWith(`${s.location} `))?.[0] ?? null;
  const rows: Array<{ place: string; when: string | null; signals: Signal[] }> = places.map(([place, when]) => ({
    place,
    when,
    signals: items.filter((s) => placeOf(s) === place),
  }));
  const other = items.filter((s) => placeOf(s) === null);
  if (other.length) rows.push({ place: places.length ? "Other checks" : "Checked", when: null, signals: other });

  return (
    <ol className="timeline">
      {rows
        .filter((r) => r.signals.length)
        .map((r) => {
          const [day, hours] = r.when ? [r.when.split(", ")[0], r.when.split(", ").slice(1).join(", ")] : [null, null];
          return (
            <li key={r.place} className="tl-row">
              <div className="tl-when">
                {hours && <span className="tl-hours">{hours}</span>}
                {day && <span className="tl-day">{day}</span>}
              </div>
              <div className="tl-what">
                <p className="tl-place">{r.place}</p>
                <ul>
                  {r.signals.map((s) => (
                    <li key={s.id} id={`ev-${s.id}`} className={highlight === s.id ? "flash" : undefined} title={s.detail}>
                      {shortTitle(s)}{" "}
                      <span className="tl-src">
                        ·{" "}
                        {s.url ? (
                          <a href={s.url} target="_blank" rel="noreferrer">
                            {s.source}
                          </a>
                        ) : (
                          s.source
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          );
        })}
    </ol>
  );
}

export function Evidence({ data, highlight }: { data: AssessResponse; highlight: string | null }) {
  // Main list: only what raises the rating. Everything else checked is one click away.
  const risky = data.signals.filter((s) => s.applies_to_travel_date && s.severity > 0);
  const context = data.signals.filter((s) => !(s.applies_to_travel_date && s.severity > 0));

  return (
    <section className="evidence" aria-labelledby="ev-h">
      <h2 id="ev-h">Evidence</h2>
      <p className="quiet">
        {risky.length
          ? `The data behind the risks, for the ${data.timing ? "hours you're travelling" : "travel date"}.`
          : `Nothing in the data raises a concern for the ${data.timing ? "hours you're travelling" : "travel date"}.`}
      </p>
      {SIDES.map((side) => {
        const items = risky.filter((s) => s.side === side);
        if (!items.length) return null;
        return (
          <div key={side} className="ev-group">
            <h3>{SIDE_LABEL[side]}</h3>
            <ul>
              {items.map((s) => (
                <Item key={s.id} s={s} highlighted={highlight === s.id} />
              ))}
            </ul>
          </div>
        );
      })}
      {context.length > 0 && (
        <details className="ev-context" open={context.some((s) => s.id === highlight)}>
          <summary>Everything else checked ({context.length})</summary>
          <p className="quiet">Checked and found normal, in trip order. Hover a line for the full forecast text.</p>
          <ContextTimeline data={data} items={context} highlight={highlight} />
        </details>
      )}
    </section>
  );
}
