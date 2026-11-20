"use client";

// "Forgot your password?": Supabase emails a link that opens /reset-password, signed in for that purpose.
// The answer is the same whether or not the email has an account, so nobody can probe who signed up.
import { useState } from "react";
import { accountsEnabled, getSupabase } from "@/lib/supabase";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const supabase = await getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    setBusy(false);
    if (error) return setError(error.message);
    setSent(true);
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
        <h1>Reset your password</h1>
        {sent ? (
          <p className="assumed" role="status">
            If there&apos;s an account for <strong>{email}</strong>, we&apos;ve sent it a link to choose a new password.
            Check your inbox (and spam folder).
          </p>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <p className="quiet">Enter your account&apos;s email and we&apos;ll send you a link to choose a new password.</p>
            <label className="field">
              Email
              <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            {error && <p className="error" role="alert">{error}</p>}
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Sending…" : "Send reset link"}
            </button>
          </form>
        )}
        <p className="quiet">
          <a href="/signin">Back to sign in</a>
        </p>
      </section>
    </main>
  );
}
