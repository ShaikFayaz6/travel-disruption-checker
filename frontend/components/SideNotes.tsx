import type { AssessResponse } from "@/lib/types";

const STATUS_TEXT = { ok: "Checked", error: "Unavailable", skipped: "Not needed", no_data: "No data" } as const;

/** The backend's AI error in plain words: "AIError: All Gemini models failed — a: … | b: …" -> "a: … ; b: …". */
function aiReason(err: string): string {
  const text = err.replace(/^\w+Error:\s*/, "").replace(/^All Gemini models failed\s*[—-]\s*/, "");
  return text.split(" | ").join("; ");
}

export function SideNotes({ data }: { data: AssessResponse }) {
  const a = data.assessment;
  return (
    <aside className="side">
      <section aria-labelledby="sure-h">
        <h2 id="sure-h">How sure is this</h2>
        <p className={`conf conf-${data.confidence}`}>
          {data.confidence === "high" ? "High" : data.confidence === "medium" ? "Medium" : "Low"} confidence
        </p>
        <p className="conf-why">{data.confidence_reason}</p>
        <p>{a.uncertainty || data.horizon_note}</p>
        {data.timing && (
          <details className="side-more">
            <summary>Hours checked</summary>
            <dl className="windows">
              {Object.entries(data.timing.windows).map(([place, w]) => (
                <div key={place}>
                  <dt>{place}</dt>
                  <dd>{w}</dd>
                </div>
              ))}
            </dl>
            <p className="quiet">{data.timing.basis}</p>
          </details>
        )}
      </section>

      <section aria-labelledby="src-h">
        <h2 id="src-h">Sources</h2>
        <ul className="sources">
          {data.sources.filter((s) => s.status !== "skipped").map((s) => (
            <li key={s.name} className={`src src-${s.status}`}>
              <span className="src-dot" aria-hidden="true" />
              <div>
                <p className="src-name">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer">
                      {s.name}
                    </a>
                  ) : (
                    s.name
                  )}
                  <span className="src-state">{STATUS_TEXT[s.status]}</span>
                </p>
                {(s.status === "error" || s.status === "no_data") && <p className="src-detail">{s.detail}</p>}
              </div>
            </li>
          ))}
        </ul>
        <p className="stamp">
          Rating by fixed rules (score {data.risk_score});{" "}
          {a.generated_by === "gemini"
            ? `text by ${a.model ?? "Gemini"} from this evidence only`
            : data.ai_pending
              ? "text by the rules for now, the AI's is on its way"
              : "text by the rules, AI unavailable"}
          . Checked {new Date(data.generated_at).toLocaleString()}.
        </p>
        {a.generated_by !== "gemini" && !data.ai_pending && a.ai_error && (
          <p className="ai-why">Why no AI text: {aiReason(a.ai_error)}</p>
        )}
      </section>
    </aside>
  );
}
