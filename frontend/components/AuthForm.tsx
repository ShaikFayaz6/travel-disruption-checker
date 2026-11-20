"use client";

// Email + password sign-up / sign-in (Supabase Auth), plus an optional "Sign in with Google".
// After it: back to the home page, or to the upgrade offer if they came from the paywall (?from=paywall).
import { useEffect, useState } from "react";
import { track } from "@/lib/analytics";
import { accountsEnabled, getSupabase, googleAuthEnabled } from "@/lib/supabase";

type Kind = "signin" | "signup";

export function AuthForm({ kind }: { kind: Kind }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [fromPaywall, setFromPaywall] = useState(false);

  useEffect(() => setFromPaywall(new URLSearchParams(window.location.search).get("from") === "paywall"), []);

  const next = fromPaywall ? "/?upgrade=1" : "/";
  const other = kind === "signin" ? "/signup" : "/signin";
  const otherHref = fromPaywall ? `${other}?from=paywall` : other;

  if (!accountsEnabled) {
    return (
      <p className="error" role="alert">
        Accounts aren&apos;t set up on this site.
      </p>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const supabase = await getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    if (kind === "signup") {
      const { data, error } = await supabase.auth.signUp({ email, password });
      setBusy(false);
      if (error) return setError(error.message);
      track("sign_up", { method: "email" });
      if (!data.session) return setNotice("Check your email to confirm your account, then sign in.");
    } else {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      setBusy(false);
      if (error) return setError(error.message);
      track("login", { method: "email" });
    }
    window.location.href = next;
  }

  async function google() {
    const supabase = await getSupabase();
    if (!supabase) return;
    try {
      sessionStorage.setItem("tc_oauth", kind); // AccountLink sends sign_up/login once they're back
    } catch {}
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}${next}` },
    });
    if (error) setError(error.message);
  }

  const title = kind === "signin" ? "Sign in" : "Create a free account";
  return (
    <section className="auth">
      <h1>{title}</h1>
      {fromPaywall && <p className="quiet">You&apos;ve used your free air check. Car checks stay free.</p>}
      <form className="auth-form" onSubmit={submit}>
        <label className="field">
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          Password
          <input
            type="password"
            autoComplete={kind === "signin" ? "current-password" : "new-password"}
            required
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {kind === "signup" && <span className="field-hint">At least 6 characters.</span>}
          {kind === "signin" && (
            <a className="field-hint" href="/forgot-password">
              Forgot your password?
            </a>
          )}
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="assumed" role="status">
            {notice}
          </p>
        )}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Please wait…" : title}
        </button>
      </form>
      {googleAuthEnabled && (
        <button type="button" className="secondary" onClick={google}>
          Continue with Google
        </button>
      )}
      <p className="quiet">
        {kind === "signin" ? "New here? " : "Already have an account? "}
        <a href={otherHref}>{kind === "signin" ? "Create a free account" : "Sign in"}</a> ·{" "}
        <a href="/">Back to Trip Check</a>
      </p>
    </section>
  );
}
