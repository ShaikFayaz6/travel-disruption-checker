"use client";

// The account page: email, plan, renewal date, "Manage billing" (Stripe Customer Portal) and "Sign out".
// The plan shown always comes from the backend (GET /api/me), which only Stripe's webhook can change:
// arriving here from Checkout (?checkout=success) never activates anything by itself.
import { useCallback, useEffect, useState } from "react";
import { apiPost, authHeaders, useSession } from "@/lib/session";
import { accountsEnabled, getSupabase } from "@/lib/supabase";

type Me = {
  email: string | null;
  plan: "free" | "pro";
  subscription_status: string | null;
  current_period_end: string | null;
  cancel_at: string | null; // cancelled: Pro until this date, then back to free
  free_air_check_used: boolean;
};

const POLL_MS = 2000;
const POLL_FOR_MS = 20000;

export default function AccountPage() {
  const { session, ready } = useSession();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<Me | null> => {
    const res = await fetch("/api/me", { headers: await authHeaders() });
    if (!res.ok) {
      setError(res.status === 404 ? "Accounts aren't switched on for this server." : `Couldn't load your account (${res.status}).`);
      return null;
    }
    const data = (await res.json()) as Me;
    setMe(data);
    return data;
  }, []);

  useEffect(() => {
    if (!session) return;
    let stop = false;
    (async () => {
      const first = await load();
      const fromCheckout = new URLSearchParams(window.location.search).get("checkout") === "success";
      if (!fromCheckout || !first || first.plan === "pro") return;
      // Stripe's webhook usually lands within a few seconds: wait for it, don't trust the redirect
      setConfirming(true);
      const until = Date.now() + POLL_FOR_MS;
      while (!stop && Date.now() < until) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const now = await load();
        if (now?.plan === "pro") break;
      }
      if (!stop) setConfirming(false);
    })();
    return () => {
      stop = true;
    };
  }, [session, load]);

  async function manageBilling() {
    setBusy(true);
    setError(null);
    try {
      const { url } = await apiPost<{ url: string }>("/api/billing/portal");
      window.location.href = url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't open billing.");
      setBusy(false);
    }
  }

  async function signOut() {
    await (await getSupabase())?.auth.signOut();
    window.location.href = "/";
  }

  if (!accountsEnabled) {
    return (
      <main className="page">
        <p className="error" role="alert">Accounts aren&apos;t set up on this site.</p>
      </main>
    );
  }
  if (ready && !session) {
    return (
      <main className="page">
        <section className="auth">
          <h1>Your account</h1>
          <p>
            <a href="/signin">Sign in</a> to see your plan.
          </p>
        </section>
      </main>
    );
  }

  const cancelling = me?.plan === "pro" && !!me.cancel_at;
  const until = cancelling ? me?.cancel_at : me?.current_period_end;
  const renews = until ? new Date(until).toLocaleDateString() : null;
  return (
    <main className="page">
      <section className="auth">
        <h1>Your account</h1>
        <p className="quiet">
          <a href="/">Back to Trip Check</a>
        </p>
        {error && <p className="error" role="alert">{error}</p>}
        {confirming && (
          <p className="ai-pending" role="status">
            <span className="spinner" aria-hidden="true" /> Confirming your payment…
          </p>
        )}
        {me && (
          <dl className="account-facts">
            <dt>Email</dt>
            <dd>{me.email ?? session?.user.email}</dd>
            <dt>Plan</dt>
            <dd>
              {me.plan === "pro" ? "Trip Check Pro: unlimited air checks" : "Free: unlimited car checks, 1 free air check"}
              {me.subscription_status && me.plan === "pro" && ` (${me.subscription_status.replace("_", " ")})`}
            </dd>
            {renews && me.plan === "pro" && (
              <>
                <dt>{cancelling ? "Ends on" : "Renews on"}</dt>
                <dd>
                  {renews}
                  {cancelling && " (cancelled: unlimited air checks until then, and you won't be charged again)"}
                </dd>
              </>
            )}
            {me.plan !== "pro" && (
              <>
                <dt>Free air check</dt>
                <dd>{me.free_air_check_used ? "Used" : "Available"}</dd>
              </>
            )}
          </dl>
        )}
        <div className="account-actions">
          {me?.plan === "pro" || me?.subscription_status ? (
            <button type="button" className="primary" onClick={manageBilling} disabled={busy}>
              Manage billing
            </button>
          ) : (
            me && (
              <a className="primary as-button" href="/?upgrade=1">
                Upgrade to Pro
              </a>
            )
          )}
          <button type="button" className="secondary" onClick={signOut}>
            Sign out
          </button>
        </div>
      </section>
    </main>
  );
}
