"use client";

// Shown in place of a result when the backend refuses an air check (HTTP 402).
// Not signed in: create a free account / sign in. Signed in: Trip Check Pro and an Upgrade button,
// which opens Stripe Checkout. Car checks are never blocked.
import { useEffect, useState } from "react";
import { gaClientId, track } from "@/lib/analytics";
import { apiPost, useSession } from "@/lib/session";

export const PLAN = {
  name: "Trip Check Pro",
  price: 4.99,
  priceLabel: "$4.99 / month",
  benefits: [
    "Unlimited air checks: every flight, every connection",
    "Live FAA status, airport forecasts and news on every air check",
    "Cancel any time from your account page",
  ],
};

export function Paywall({ code, message }: { code: string; message: string }) {
  const { session, ready } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => track("paywall_shown", { reason: code }), [code]);

  async function upgrade() {
    setBusy(true);
    setError(null);
    track("begin_checkout", {
      value: PLAN.price,
      currency: "USD",
      items: [{ item_id: "tripcheck_pro_monthly", item_name: PLAN.name, price: PLAN.price, quantity: 1 }],
    });
    try {
      const { url } = await apiPost<{ url: string }>("/api/billing/checkout", { ga_client_id: gaClientId() });
      window.location.href = url; // Stripe's hosted Checkout page
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start the checkout.");
      setBusy(false);
    }
  }

  if (!ready) return null;
  const signedIn = !!session;
  return (
    <section className="paywall" aria-labelledby="paywall-title">
      <h2 id="paywall-title">{signedIn ? `Upgrade to ${PLAN.name}` : "Create a free account to continue"}</h2>
      <p>{signedIn && code === "free_limit_reached" ? "You've used your free air check." : message}</p>
      {signedIn ? (
        <>
          <p className="paywall-price">{PLAN.priceLabel}</p>
          <ul>
            {PLAN.benefits.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          <div className="paywall-actions">
            <button type="button" className="primary" onClick={upgrade} disabled={busy}>
              {busy ? "Opening checkout…" : "Upgrade"}
            </button>
            <span className="quiet">Car checks stay free.</span>
          </div>
        </>
      ) : (
        <div className="paywall-actions">
          <a className="primary as-button" href="/signup?from=paywall">
            Create free account
          </a>
          <a href="/signin?from=paywall">Sign in</a>
          <span className="quiet">Car checks stay free, with no account.</span>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
