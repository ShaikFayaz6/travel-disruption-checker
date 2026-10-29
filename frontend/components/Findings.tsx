import type { Action, AssessResponse, Signal } from "@/lib/types";

const PRIORITY: Record<Action["priority"], string> = {
  now: "Now",
  before_trip: "Before you go",
  day_of: "On the day",
  optional: "Optional",
};
const ORDER: Action["priority"][] = ["now", "before_trip", "day_of", "optional"];

export function Findings({ data, onShowEvidence }: { data: AssessResponse; onShowEvidence: (id: string) => void }) {
  const byId = new Map<string, Signal>(data.signals.map((s) => [s.id, s]));
  const { risks, actions } = data.assessment;
  const sortedActions = [...actions].sort((a, b) => ORDER.indexOf(a.priority) - ORDER.indexOf(b.priority));

  return (
    <div className="findings">
      <section aria-labelledby="risks-h">
        <h2 id="risks-h">What could go wrong</h2>
        {risks.length === 0 ? (
          <p className="quiet">Nothing in the current data points to a meaningful disruption on this trip.</p>
        ) : (
          <ul className="risks">
            {risks.map((r) => (
              <li key={r.title} className={`risk sev-${r.severity}`}>
                <div className="risk-head">
                  <h3>{r.title}</h3>
                  <span className="sev-tag">{r.severity === "moderate" ? "Moderate risk" : r.severity === "high" ? "High risk" : "Low risk"}</span>
                </div>
                <p>{r.explanation}</p>
                <p className="cites">
                  Based on{" "}
                  {r.evidence_ids.map((id, i) => {
                    const s = byId.get(id);
                    return (
                      <span key={id}>
                        <button type="button" className="cite" onClick={() => onShowEvidence(id)}
                          title={s ? `${s.source}, ${s.location}` : undefined}>
                          {s ? `${s.title} (${s.source})` : id}
                        </button>
                        {i < r.evidence_ids.length - 1 ? "; " : ""}
                      </span>
                    );
                  })}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="actions-h">
        <h2 id="actions-h">What to do</h2>
        <ol className="actions">
          {sortedActions.map((a) => (
            <li key={a.action}>
              <span className="when">{PRIORITY[a.priority]}</span>
              <div>
                <p className="act">{a.action}</p>
                {a.why && <p className="why">{a.why}</p>}
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
