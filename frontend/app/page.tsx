"use client";

import { useEffect, useRef, useState } from "react";
import { Evidence } from "@/components/Evidence";
import { Findings } from "@/components/Findings";
import { RouteStrip } from "@/components/RouteStrip";
import { SideNotes } from "@/components/SideNotes";
import { TripForm, TripInput, isoDate } from "@/components/TripForm";
import { assessTrip, type AssessResponse } from "@/lib/types";

export default function Home() {
  const [input, setInput] = useState<TripInput>({ origin: "", destination: "", date: "", time: "", mode: "fly", legs: [] });
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<TripInput | null>(null);
  const [data, setData] = useState<AssessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);

  // date default is set on the client to avoid a server/client timezone mismatch
  useEffect(() => setInput((v) => (v.date ? v : { ...v, date: isoDate(1) })), []);

  const runId = useRef(0); // a newer check replaces an older one still streaming its AI answer

  async function run(v: TripInput) {
    const id = ++runId.current;
    setBusy(true);
    setError(null);
    setPending(v);
    setData(null);
    setHighlight(null);
    let first = true;
    try {
      // progressive: the first answer (rules only) shows in ~2 s, the AI's replaces it when ready
      await assessTrip(v, (res) => {
        if (id !== runId.current) return;
        setData(res);
        setBusy(false);
        setPending(null);
        if (!first) return;
        first = false;
        // with chosen flights, the time to leave home is worked out: show it in the form, rounded down
        // to the quarter hour the dropdown offers (a few minutes early is the safe side)
        const leave = res.timing?.leave_local;
        if (v.legs.length && leave) {
          const [h, m] = leave.slice(11, 16).split(":").map(Number);
          const time = `${String(h).padStart(2, "0")}:${String(m - (m % 15)).padStart(2, "0")}`;
          setInput((cur) => (cur.legs === v.legs ? { ...cur, date: leave.slice(0, 10), time } : cur));
        }
      });
    } catch (e) {
      if (id !== runId.current) return;
      if (first) setError(e instanceof Error ? e.message : "Something went wrong.");
      else setData((d) => (d ? { ...d, ai_pending: false } : d)); // keep the first answer
    } finally {
      if (id === runId.current) {
        setBusy(false);
        setPending(null);
        setData((d) => (d?.ai_pending ? { ...d, ai_pending: false } : d)); // stream ended without the AI's
      }
    }
  }

  function showEvidence(id: string) {
    setHighlight(id);
    requestAnimationFrame(() =>
      document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }),
    );
  }

  return (
    <main className="page">
      <header className="masthead">
        <p className="brand">Trip Check</p>
        <p className="tagline">Will anything get in the way of this trip? Weather, airport status and news, checked in one place.</p>
      </header>

      <TripForm value={input} onChange={setInput} onSubmit={run} busy={busy} />

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {pending && <RouteStrip pending={{ origin: pending.origin, destination: pending.destination }} />}

      {!pending && !data && !error && (
        <section className="empty">
          <p>Enter where you&apos;re leaving from, where you&apos;re going, and when you depart.</p>
          <p className="quiet">
            Live data from the National Weather Service, Open-Meteo, AviationWeather.gov, the FAA and Google News.
          </p>
        </section>
      )}

      {data && (
        <>
          <RouteStrip data={data} />
          <div className="results">
            <div className="main-col">
              <Findings data={data} onShowEvidence={showEvidence} />
              <Evidence data={data} highlight={highlight} />
            </div>
            <SideNotes data={data} />
          </div>
        </>
      )}
    </main>
  );
}
