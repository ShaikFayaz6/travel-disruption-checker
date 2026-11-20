# Stripe: setup and testing (test mode only)

Trip Check Pro is a **$4.99/month subscription** sold with Stripe Checkout. Stripe's webhook is the
only thing that switches a user to Pro (or back to free).

## 1. One-time setup

1. In the Stripe dashboard, turn on **Test mode**.
2. Developers → API keys → copy the **Secret key** (`sk_test_...`) into `backend/.env` as `STRIPE_SECRET_KEY`.
3. Settings → Billing → **Customer portal** → **Activate** (the default settings are fine).
4. Create the product and price (safe to run again; it refuses a live key):
   ```bash
   cd backend
   python scripts/stripe_setup.py
   ```
   Copy the printed `STRIPE_PRICE_ID=price_...` into `backend/.env`.

## 2. Test on your computer

The webhook must reach your backend. The Stripe CLI forwards events from Stripe to your computer:

```bash
stripe login
stripe listen --forward-to localhost:8000/api/stripe/webhook
```

It prints `Your webhook signing secret is whsec_...`. Put that in `backend/.env` as
`STRIPE_WEBHOOK_SECRET`, then restart the backend (`uvicorn app.main:app --reload --port 8000`).
Keep `stripe listen` running while you test.

### Test cards

| Card number | What happens |
|---|---|
| `4242 4242 4242 4242` | Payment succeeds |
| `4000 0000 0000 0002` | Card declined |

Use any future expiry date (e.g. `12/34`), any 3-digit CVC, and any ZIP code.

### The test journey

1. Run an air check without signing in: it works (your free check).
2. Run a second air check: the paywall says "Create a free account".
3. Sign up: the paywall now offers **Upgrade** (signing up doesn't give a second free check).
4. Upgrade → Stripe Checkout → pay with `4242...`.
5. You land on `/account`. It shows "Confirming your payment…" until the webhook arrives (usually
   1–3 s), then **Trip Check Pro**. In the `stripe listen` window you'll see
   `checkout.session.completed → 200`.
6. Air checks are now unlimited.
7. `/account` → **Manage billing** → cancel the plan. The webhook
   (`customer.subscription.updated`) records the end date: the account page shows "Ends on <date>",
   and air checks stay unlimited until then (the month is paid for). When that date comes, Stripe sends
   `customer.subscription.deleted` and the user is back on the free plan.

To test the end of a period without waiting a month, cancel **immediately** in the dashboard
(Customers → the customer → the subscription → Cancel → "Immediately").

### Check that the success page can't be faked

Open `http://localhost:3000/account?checkout=success` by hand while on the free plan. It shows
"Confirming your payment…" for 20 seconds, then stays on **Free**. Nothing is activated, because
only the signed webhook can change the plan.

## 3. Production (Render)

Billing calls from the page (`/api/billing/checkout`, `/api/billing/portal`, `/api/me`) go through
Vercel's existing `/api` rewrite to the backend. **Stripe itself must call the Render URL directly.**

1. Stripe dashboard (test mode) → Developers → **Webhooks** → **Add endpoint**.
2. Endpoint URL: `https://<your-render-service>.onrender.com/api/stripe/webhook`
3. Select these 4 events:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
4. Save, then reveal the endpoint's **Signing secret** (`whsec_...`) and set it on Render as
   `STRIPE_WEBHOOK_SECRET`. It is different from the one `stripe listen` prints.
5. On Render, also set `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID` and
   `FRONTEND_URL=https://<your-app>.vercel.app`.

## How the webhook stays safe

- **Signature check:** every request is checked with `stripe.Webhook.construct_event()` against the
  raw body and `STRIPE_WEBHOOK_SECRET`. Anything else gets HTTP 400.
- **Each event once:** the event ID is stored in `stripe_events` first. If Stripe sends it again,
  we answer 200 and do nothing. If handling fails, the ID is removed so Stripe's retry is processed.
- **Period end:** newer Stripe API versions put `current_period_end` on the subscription item
  (`items.data[0].current_period_end`), older ones on the subscription. Both are read.
