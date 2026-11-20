# Build log: accounts, paywall, payments and analytics

This file explains, phase by phase, what was added to Trip Check, which files changed, and why.
It is written in plain English so it can be used to present the work.

Everything new is **off by default**. With none of the new environment variables set, Trip Check
works exactly as before: no sign-in link, no paywall, no tracking.

---

## Phase 0: Setup

**What:** a new git branch (`feature/accounts-paywall`) and the libraries the next phases need.

**Files:**
- `backend/requirements.txt` and `backend/constraints.txt`: added `asyncpg` (talks to the Postgres
  database), `PyJWT[crypto]` (checks sign-in tokens) and `stripe` (payments). `constraints.txt` pins
  their exact versions, the same way the existing packages are pinned.
- `frontend/package.json`: added `@supabase/supabase-js` (sign-up and sign-in in the browser) and
  `@next/third-parties` (the official way to load Google Tag Manager in Next.js).
- `backend/.env.example` and a new `frontend/.env.example`: every new setting, with an empty value
  and a one-line comment. Real keys go only in `backend/.env` and `frontend/.env.local`.
- `.gitignore`: now also ignores `.env.local` files, so frontend keys can't be committed by mistake.

**Why:** each later phase needs these, and keeping the setting names in the example files means
anyone can see what to configure without reading the code.

---

## Phase 1: Database (Supabase Postgres)

**What:** three tables in Supabase's Postgres database, and a small backend module that talks to it.

**Files:**
- `supabase/migrations/001_accounts_paywall.sql`: creates the tables. You paste it into Supabase's
  SQL Editor once (see `docs/DB_SETUP.md`).
  - `profiles`: one row per user: their plan (`free` or `pro`) and their Stripe subscription status.
    A database trigger creates it automatically when someone signs up.
  - `checks`: one row per check that produced an answer: air or car, the cities, the date, and who ran
    it. This is how we know whether someone has used their free air check.
  - `stripe_events`: the ID of every payment event Stripe sent us, so each is handled only once.
  - Row Level Security is switched on, so a browser can only ever read its own profile.
- `backend/app/db.py` (new): one function per database action (`get_profile`, `fly_check_used`,
  `insert_check`, `update_subscription`, ...). When `DATABASE_URL` is empty, every function does nothing.
- `backend/app/config.py`: reads the new settings (`DATABASE_URL`, `SUPABASE_URL`, Stripe and GA4 keys)
  and works out the switches `PAYWALL_ENABLED` and `BILLING_ENABLED`.
- `backend/app/main.py`: 3 lines that open the database connection pool at startup and close it at shutdown.

**Why these choices:**
- **Supabase** gives us a real Postgres database *and* user accounts in one free service. Render's own
  disk is wiped on every deploy, so we can't keep data on the server itself.
- **`asyncpg`** is a fast Postgres driver that works with FastAPI's async code. It is set up with
  `statement_cache_size=0` because Supabase's connection pooler can send each query to a different
  connection.
- **Store a hash of the IP, never the IP itself.** We need to recognise the same network again (to
  stop someone clearing their browser to get another free check), but we don't need to know the address.

---

## Phase 2: Sign-up and sign-in

**What:** people can create an account and sign in with email and password (and with Google, if
switched on). The backend can tell who is asking.

**Backend files:**
- `backend/app/auth.py` (new):
  - `current_user()` reads the `Authorization: Bearer <token>` header. The token is a JWT that Supabase
    gave the browser at sign-in. We check its signature with Supabase's public keys (JWKS), or with the
    older shared secret if `SUPABASE_JWT_SECRET` is set, and that it was made for signed-in users
    (`aud = "authenticated"`). A missing, expired or fake token just means "anonymous". It never
    causes an error, so visitors without an account keep working.
  - `anon_id()` reads the `X-Anon-Id` header: a random ID the browser keeps for itself.
  - `ip_hash()` takes the visitor's IP (the first one in `X-Forwarded-For`, because Render and Vercel
    sit in front of us), adds a secret salt and hashes it with SHA-256. The raw IP is never stored.

**Frontend files (all new, except one line in `page.tsx`):**
- `lib/supabase.ts`: the Supabase client, or `null` when the `NEXT_PUBLIC_SUPABASE_*` settings are empty.
- `lib/anon.ts`: `getAnonId()` makes a random ID once and keeps it in `localStorage` (`tc_anon_id`).
- `lib/session.ts`: `useSession()` (who is signed in, updated live), `authHeaders()` (the two headers
  above) and `apiPost()` (calls the backend as the signed-in user).
- `lib/analytics.ts`: `track()`, used from Phase 5 on (it does nothing until GTM is set up).
- `components/AuthForm.tsx` with `app/signin/page.tsx` and `app/signup/page.tsx`: email + password
  forms with labelled inputs and errors read out by screen readers (`role="alert"`). If the visitor came
  from the paywall, they go back to the upgrade offer afterwards (`/?upgrade=1`).
- `app/account/page.tsx`: email, plan, renewal date, "Manage billing" and "Sign out".
- `components/AccountLink.tsx`: "Sign in", or your email, in the top-right corner. It shows nothing
  when accounts aren't configured. `app/page.tsx` got one line to show it.
- `app/globals.css`: a few new classes at the end, for these pages and the paywall card.

**Why headers instead of cookies:** in production the page is on Vercel and the check is streamed
straight from Render (a different site). Browsers block or limit cookies between different sites, but
they always send headers that the page adds itself. So the browser sends who it is with each request.

---

## Phase 3: The free air-check gate (enforced by the backend)

**What:** every visitor gets **one free air check**. After that, an air check needs a free account
*and* Trip Check Pro. Car checks are always free, with no account.

**How it decides** (`backend/app/gate.py`, new):
1. Paywall switched off, or a car check → allowed.
2. Signed in with an active Pro subscription whose paid period hasn't ended → allowed.
3. Has the free air check not been used yet → allowed (this is their free one). "Not used" means none
   of these has an air check in the `checks` table: this browser's anonymous ID, this network's IP
   hash, or (if signed in) the account. So clearing the browser doesn't give a second free check,
   and neither does signing up.
4. Otherwise → blocked with HTTP 402 and a code: `free_limit_reached` (not signed in: "create a free
   account") or `subscription_required` (signed in: "upgrade").

**When a check counts:** only after its first answer was produced. A check that fails (say, a
place that doesn't exist) or is blocked doesn't use up the free check. Car checks are recorded too,
for usage numbers.

**Changes to existing files (kept small):**
- `backend/app/assess.py`: `assess_stream()` and `assess()` take an optional `on_mode` callback. It is
  called as soon as the travel mode is known (an "auto" trip over 300 miles becomes "fly"), and
  *before* any weather, airport or news data is fetched. So a blocked check costs almost nothing,
  and we don't repeat the place lookup to find out the mode. Callers that don't pass it (all the
  existing tests) work exactly as before.
- `backend/app/main.py`: both check endpoints pass the gate's callback, turn `PaywallBlocked` into
  HTTP 402, and record the check after the first answer.
- `frontend/lib/types.ts`: `assessTrip()` sends the identity headers, and throws a new `PaywallError`
  on HTTP 402.
- `frontend/app/page.tsx`: about 10 lines that show the new `components/Paywall.tsx` card instead
  of an error.

**Why on the backend:** anything the browser decides, a user can change in the developer tools.
The server holds the count, so the limit is real, and the paid feature can't be unlocked from the page.

**Decisions noted:**
- If the database is down, the gate *allows* the check and logs the error. A paying customer should
  never be locked out because of our outage, and one extra free check costs very little.
- Several people behind the same office network share an IP hash, so only the first of them gets the
  free air check. That's acceptable for 1 free check (they can sign up), and it is what stops someone
  getting unlimited free checks by clearing their browser.
- `Authorization` and `X-Anon-Id` work cross-origin as they are: the existing CORS setting
  (`allow_headers=["*"]`) answers the browser's pre-flight request with exactly the headers it asks for.

---

## Phase 4: Stripe subscriptions (the webhook is the source of truth)

**What:** signed-in users can buy **Trip Check Pro ($4.99/month)** with Stripe Checkout, manage or
cancel it in Stripe's Customer Portal, and get unlimited air checks while it is active.

**Files:**
- `backend/scripts/stripe_setup.py` (new): creates the product and the monthly price in Stripe test
  mode, and prints the price ID. Running it twice doesn't make duplicates (it finds the price by its
  `lookup_key`). It refuses to run with a live key.
- `backend/app/billing.py` (new), mounted with one `include_router` line in `main.py`:
  - `POST /api/billing/checkout`: finds or creates the user's Stripe customer, then creates a Checkout
    Session in subscription mode and returns its URL. The browser just goes to that URL: no Stripe
    JavaScript needed on our page. The user's ID travels with the session (`client_reference_id`
    and metadata), so the webhook knows who paid.
  - `POST /api/billing/portal`: a link to Stripe's Customer Portal (change card, invoices, cancel).
  - `POST /api/stripe/webhook`: Stripe tells us what happened. We check the signature, skip events
    we've already handled, and update the user's profile: `checkout.session.completed` → Pro,
    `customer.subscription.updated` / `.deleted` → new status (back to free when canceled or unpaid),
    `invoice.payment_failed` → `past_due`.
  - `GET /api/me`: the signed-in user's plan, status, renewal date and whether the free air check is used.
- `backend/app/ga4.py` (new): sends the `purchase` event to GA4 from the server (see Phase 5).
- `frontend/components/Paywall.tsx`: the **Upgrade** button calls the checkout route and redirects.
- `frontend/app/account/page.tsx`: **Manage billing** opens the portal. After Checkout it shows
  "Confirming your payment…" and asks the backend again every 2 s for up to 20 s.
- `docs/STRIPE_TESTING.md` (new): how to test locally with the Stripe CLI and test cards, and how to
  register the production webhook on Render.

**Why the webhook, not the success page:** the success page is just a URL. Anyone can type
`/account?checkout=success` without paying, and a real payer might close the tab before it loads.
Stripe's webhook is signed (we can prove it came from Stripe) and is retried until we answer, so it
is the only reliable signal that money moved.

---

## Phase 5: GA4 + Google Tag Manager

**What:** analytics for the whole sign-up and payment journey, so we can see where people drop out.

**Files:**
- `frontend/app/layout.tsx`: 2 lines that load Google Tag Manager when `NEXT_PUBLIC_GTM_ID` is set,
  using Next.js's own `<GoogleTagManager>` component. It loads after the page becomes interactive,
  so it doesn't slow down the first paint (LCP, a Core Web Vital).
- `frontend/lib/analytics.ts` (added in Phase 2): `track(event, params)` pushes an event to GTM's
  `dataLayer`. It does nothing when GTM is off. It never sends personal data (no emails, no addresses).
- Where each event fires:
  - `check_started` and `check_completed` (with how long the first answer took): `app/page.tsx` (3 lines).
  - `paywall_shown`, `begin_checkout`: `components/Paywall.tsx`.
  - `sign_up`, `login`: `components/AuthForm.tsx`. For Google sign-in, `components/AccountLink.tsx`
    sends it once the user comes back from Google.
  - `purchase`: **from the server**, `backend/app/ga4.py`, called by the Stripe webhook. It uses GA4's
    Measurement Protocol, a 3-second timeout, and never blocks or breaks the webhook.
- `docs/GTM_SETUP.md` (new): every click in GTM and GA4, how to check it in GTM Preview and GA4
  DebugView, and how to build the funnel check → paywall → sign-up → checkout → purchase.

**Why the purchase is server-side:** the browser can miss it (tab closed, ad blocker) or fake it
(open the success page without paying). The webhook only runs when Stripe has the money, so the
revenue number in GA4 matches Stripe. The browser's GA4 client ID is passed through Checkout, so the
purchase still joins the same visitor's journey.

---

## Phase 6: Sanity CMS (skipped)

The plan made this optional, to do only if `NEXT_PUBLIC_SANITY_PROJECT_ID` was set. It wasn't, so the
paywall uses the copy in `components/Paywall.tsx` (the `PLAN` object: name, price, benefits). That
object is shaped like the planned Sanity `pricingPlan` document, so adding Sanity later only means
fetching it and falling back to `PLAN` on any error.

---

## Phase 7: Tests, docs, finish

**Tests** (all offline: the database, Stripe and the network are faked):
- `backend/tests/test_gate.py`: the free air-check rule. Car checks are always allowed. The first
  anonymous air check is free. A second one from the same browser, or from a new browser on the same
  network, is blocked. A signed-in user who used it needs a subscription, and signing up doesn't give
  another one. Active Pro is allowed. Expired or canceled Pro is blocked. Paywall off → always allowed.
- `backend/tests/test_auth.py`: no token or a garbage, forged, expired or wrong-audience token →
  anonymous. A valid token → the user. The anonymous ID must be a UUID. The IP is hashed.
- `backend/tests/test_billing_webhook.py`: a bad signature → 400. A valid signed event → the profile
  is updated. The same event twice → handled once. Checkout completed → Pro, plus the GA4 purchase.
  `current_period_end` is read from both places Stripe puts it.
- `backend/tests/test_paywall_endpoints.py`: a blocked air check → HTTP 402 with the right body, on
  both endpoints, and isn't counted. A successful check is counted once. A failed one isn't. A blocked
  check fetches no weather, airport or news data.
- All 76 tests that existed before still pass, unchanged.

**A performance fix found while testing:** the first version loaded the Supabase library on every
page view, even with accounts switched off, adding ~66 kB of JavaScript to the home page
(122 kB → 188 kB first load). `lib/supabase.ts` now loads it only when it's needed. The home page is
back to 122 kB, and the sign-in code doesn't slow down the check page's first paint.

**Also added:** `backend/scripts/db_migrate.py` runs the SQL migration against `DATABASE_URL`
(the same as pasting it into the SQL Editor).

---

## Summary (for a founder)

Trip Check was a free tool with no way to earn money. Now it has a simple subscription model.
Anyone can try it: car checks are always free, and the first air check needs no account, so a new
visitor sees the product working before being asked for anything. The second air check asks them to
create a free account, then to upgrade to **Trip Check Pro for $4.99 a month**, paid through Stripe.
Payments, cancellations and failed cards are all handled by Stripe and reported back to us
automatically, so nobody has to update accounts by hand. Every step of that journey (check, paywall,
sign-up, checkout, purchase) is measured in Google Analytics, so we can see where people drop out and
what to improve. Everything is switched off until its keys are set, so none of it puts the existing
product at risk.

## Architecture

```
                         ┌──────────────────────────────┐
  Browser ──────────────▶│ Vercel: Next.js (the page)   │──── GTM (dataLayer events) ──▶ GA4
     │                   │  /signin /signup /account    │
     │                   └──────────────┬───────────────┘
     │  check stream (direct,           │ /api/* rewrite
     │  Authorization + X-Anon-Id)      │ (/api/me, /api/billing/*)
     ▼                                  ▼
  ┌───────────────────────────────────────────────────┐        ┌─────────────────────────┐
  │ Render: FastAPI                                   │◀──────▶│ Supabase                │
  │  auth.py  (who is asking: JWT, anon ID, IP hash)  │  SQL   │  Auth (sign-up/sign-in) │
  │  gate.py  (1 free air check, then Pro)            │        │  Postgres: profiles,    │
  │  billing.py (Checkout, Portal, webhook, /api/me)  │        │  checks, stripe_events  │
  └───────────▲───────────────────────┬───────────────┘        └─────────────────────────┘
              │ signed webhook         │ Measurement Protocol
              │ (Render URL directly)  ▼ (server-side purchase)
           Stripe                     GA4
```

The browser signs in with Supabase directly. Supabase gives it a token (a JWT), which it sends to
our backend, and the backend checks it.

## The 3 most important technical decisions

**1. The limit is enforced by the backend, not the page.**
- *Option A, frontend only:* quick to build, but anyone can change it in the browser's developer
  tools, or call the API directly.
- *Option B, backend (chosen):* the server keeps the count in the database and refuses the check
  with HTTP 402. It needed a database and identity headers, but it is the only way the paid feature
  is really paid for. The check is stopped right after the travel mode is known, before any data is
  fetched, so a blocked check costs us almost nothing.
- *Trade-off:* people sharing an office network share the IP hash, so only the first of them gets
  the free check. That's acceptable for one free check, and it's what stops "clear the browser, get
  another free check".

**2. Stripe's webhook activates the subscription, not the success page.**
- *Option A, success URL:* simple, but anyone can type `/account?checkout=success` without paying,
  and a real customer who closes the tab too early would never be activated.
- *Option B, webhook (chosen):* Stripe calls our server, signed with a secret, and retries until we
  answer. We store each event ID so a retry is never applied twice. The success page only waits
  ("Confirming your payment…") and shows what the server says.
- *Trade-off:* there is a short wait (usually 1–3 s) after paying, and local testing needs the Stripe
  CLI to forward webhooks. Both are worth it for a payment state that is always right.

**3. Who is asking is sent in headers, not cookies.**
- The page is on Vercel and the check streams straight from Render (a different site), because
  Vercel's proxy would hold back the streamed lines. Browsers block or limit cookies between
  different sites.
- *Chosen:* the browser sends `Authorization: Bearer <Supabase token>` and `X-Anon-Id` itself, on
  every request. It works the same through the proxy and on the direct stream, and the existing CORS
  setting already allows it.
- *Trade-off:* the token lives in the browser's storage (Supabase's default), not in an `httpOnly`
  cookie, so the page must stay free of injected scripts. React escapes everything it renders, and the
  token only grants access to the user's own plan.

---

## Added after testing: password reset and "Continue with Google"

**What:** users who forget their password can reset it by email, and the Google button now reads
"Continue with Google" because the same button signs people up *or* in.

**Files:**
- `frontend/app/forgot-password/page.tsx` (new): asks for the email and calls Supabase's
  `resetPasswordForEmail()`. It always shows the same message, whether or not the email has an
  account, so the form can't be used to find out who has signed up.
- `frontend/app/reset-password/page.tsx` (new): the email link opens this page. Supabase reads the
  token in the link and signs the user in for this purpose only, then `updateUser({ password })`
  saves the new password. An old or broken link gets "This reset link is invalid or has expired".
- `frontend/components/AuthForm.tsx`: a "Forgot your password?" link under the password field on the
  sign-in page, and the Google button's new label.
- `frontend/components/AccountLink.tsx`: if Supabase sends a reset link to the home page (when
  `/reset-password` isn't in its allowed redirect URLs), the home page passes it on to the reset page.
- `docs/AUTH_SETUP.md` (new): the Supabase settings, and how to connect Google (a Google Cloud OAuth
  client + the Supabase Google provider).

---

## Added after testing: showing a cancelled plan correctly

**Found while testing:** after cancelling in Stripe's Customer Portal, the user kept unlimited air
checks (correct: they paid for the month), but the account page still said "Renews on", which was
wrong.

**Why:** cancelling in the portal doesn't end the subscription straight away. Stripe keeps it
`active` and sets `cancel_at` to the end of the paid period. On that date it sends
`customer.subscription.deleted`, and only then does the user go back to the free plan.

**Files:**
- `supabase/migrations/002_cancel_at.sql` (new): a `cancel_at` column on `profiles`.
- `backend/app/billing.py`: the webhook stores `cancel_at` (or the period end, for Stripe's older
  `cancel_at_period_end` flag), and clears it if the user turns renewal back on. `/api/me` returns it.
- `backend/app/db.py`: `update_subscription()` can set or clear `cancel_at`.
- `frontend/app/account/page.tsx`: shows "Ends on <date> (cancelled: unlimited air checks until then,
  and you won't be charged again)" instead of "Renews on".
- `backend/tests/test_billing_webhook.py`: 3 new tests (cancel date stored, older flag, renewal clears it).

---

## Added after deploying: the flight list is behind the paywall too

**Found on the live site:** after the free air check was used, pressing *Check trip* showed the paywall,
but the flight list above it still loaded. Each flight search is a paid AeroDataBox request, so a
blocked visitor could keep using the flight-data quota, and the page looked half-unlocked.

**Fix:** the flight lookups now follow the same rule as the check.
- `backend/app/gate.py`: `require_fly(request)` runs the free-air-check rule without recording anything.
- `backend/app/main.py`: `/api/flights` and `/api/flight` call it first and answer **402** before any
  flight-data request is made.
- `frontend/lib/types.ts`: the flight calls send the same identity headers as the check, and a 402
  becomes a `PaywallError`.
- `frontend/components/FlightLegs.tsx`, `TripForm.tsx`, `app/page.tsx`: a refused lookup shows the
  paywall, and the flight list is hidden while the paywall is showing.
- `backend/tests/test_paywall_endpoints.py`: 4 new tests (blocked → 402 and the flight API isn't called;
  allowed → 200), on both endpoints. 115 tests pass.
