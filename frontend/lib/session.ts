// The signed-in user (useSession) and the headers that tell the backend who is asking (authHeaders).
// Headers, not cookies: in production the check is streamed straight to the backend on another site.
import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { getAnonId } from "./anon";
import { accountsEnabled, getSupabase } from "./supabase";

export function useSession(): { session: Session | null; ready: boolean } {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(!accountsEnabled);
  useEffect(() => {
    let unsubscribe = () => {};
    let gone = false;
    getSupabase().then((supabase) => {
      if (!supabase || gone) return;
      supabase.auth.getSession().then(({ data }) => {
        setSession(data.session);
        setReady(true);
      });
      const { data } = supabase.auth.onAuthStateChange((_event, s) => setSession(s));
      unsubscribe = () => data.subscription.unsubscribe();
    });
    return () => {
      gone = true;
      unsubscribe();
    };
  }, []);
  return { session, ready };
}

export async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
  const anon = getAnonId();
  if (anon) headers["X-Anon-Id"] = anon;
  const supabase = await getSupabase();
  if (supabase) {
    const { data } = await supabase.auth.getSession(); // refreshes an expired token first
    if (data.session) headers["Authorization"] = `Bearer ${data.session.access_token}`;
  }
  return headers;
}

// Calls a backend route through the same-origin /api proxy, as the signed-in user.
export async function apiPost<T>(path: string, body: unknown = {}): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `The server returned ${res.status}.`);
  return data as T;
}
