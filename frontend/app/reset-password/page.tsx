"use client";

// Opened from the password-reset email. Supabase reads the link's token and signs the user in
// (the PASSWORD_RECOVERY event); then they choose a new password.
import { useEffect, useState } from "react";
import { accountsEnabled, getSupabase } from "@/lib/supabase";

const WAIT_FOR_LINK_MS = 4000;

export default function ResetPasswordPage() {
  const [state, setState] = useState<"checking" | "ready" | "invalid" | "done">("checking");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let unsubscribe = () => {};
    const timer = setTimeout(() => setState((s) => (s === "checking" ? "invalid" : s)), WAIT_FOR_LINK_MS);
    getSupabase().then((supabase) => {
      if (!supabase) return;
      const { data } = supabase.auth.onAuthStateChange((event, session) => {
        if (event === "PASSWORD_RECOVERY" || session) setState((s) => (s === "checking" ? "ready" : s));
      });
      unsubscribe = () => data.subscription.unsubscribe();
    });
    return () => {
      clearTimeout(timer);
      unsubscribe();
    };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) return setError("The two passwords don't match.");
    const supabase = await getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (error) return setError(error.message);
    setState("done");
  }

  if (!accountsEnabled) {
    return (
      <main className="page">
        <p className="error" role="alert">Accounts aren&apos;t set up on this site.</p>
      </main>
    );
  }
  return (
    <main className="page">
      <section className="auth">
        <h1>Choose a new password</h1>
        {state === "checking" && (
          <p className="ai-pending" role="status">
            <span className="spinner" aria-hidden="true" /> Checking your reset link…
          </p>
        )}
        {state === "invalid" && (
          <p className="error" role="alert">
            This reset link is invalid or has expired. <a href="/forgot-password">Send a new one</a>.
          </p>
        )}
        {state === "ready" && (
          <form className="auth-form" onSubmit={submit}>
            <label className="field">
              New password
              <input type="password" autoComplete="new-password" required minLength={6} value={password}
                onChange={(e) => setPassword(e.target.value)} />
              <span className="field-hint">At least 6 characters.</span>
            </label>
            <label className="field">
              Type it again
              <input type="password" autoComplete="new-password" required minLength={6} value={confirm}
                onChange={(e) => setConfirm(e.target.value)} />
            </label>
            {error && <p className="error" role="alert">{error}</p>}
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Saving…" : "Save new password"}
            </button>
          </form>
        )}
        {state === "done" && (
          <p className="assumed" role="status">
            Your password has been changed and you&apos;re signed in. <a href="/">Back to Trip Check</a>
          </p>
        )}
      </section>
    </main>
  );
}
