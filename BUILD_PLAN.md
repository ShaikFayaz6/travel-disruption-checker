# Trip Check — Accounts, Paywall, Stripe & Analytics Build Plan

---

## PART A — For Fayaz (read this first, about 20 minutes of setup)

### How to use this file
1. Save this file as `BUILD_PLAN.md` in the root of your `travel-disruption-checker` repo.
2. Do the account setup below **while** Claude Code works on Phases 0–2. Claude Code can write the code, but it can't create accounts for you.
3. Open Claude Code in the repo folder and paste this message:

   > Read BUILD_PLAN.md, Part B. Do every phase in order without asking me questions. All decisions are already made in the file. Commit after each phase. When a phase needs a key I haven't given you yet, use the env var name from the file and keep going. The feature stays switched off until the key is set.

4. When Claude Code finishes, paste your keys into `backend/.env` and `frontend/.env.local` (the lists are in Part B, section "Environment variables"), restart, and go through the "Done checklist" at the end.

### Accounts to set up (all free, all test mode)

**1. Supabase (database + login)**
- Go to supabase.com → New project. Pick a region near Texas (for example East US).
- Project Settings → API: copy the **Project URL**, the **publishable/anon key** and the **secret/service_role key**.
- Project Settings → Database → Connection string → choose **Session pooler** (not "Direct": Render can't reach it). Copy the URI and put your database password in it.
- Authentication → Sign In / Providers → Email: for faster testing, turn **off** "Confirm email". Turn it back on before real users.
- Authentication → URL Configuration: add `http://localhost:3000` and your Vercel URL to Redirect URLs.

**2. Stripe (payments)**
- dashboard.stripe.com → make sure **Test mode** is on.
- Developers → API keys: copy the **Secret key** (`sk_test_...`).
- Install the Stripe CLI (stripe.com/docs/stripe-cli), then run `stripe login`. You need it to test webhooks locally.
- Settings → Billing → Customer portal → click **Activate** (default settings are fine).

**3. Google Analytics 4 + Google Tag Manager**
- analytics.google.com → create a GA4 property and a Web data stream. Copy the **Measurement ID** (`G-XXXXXXX`).
- In that data stream → Measurement Protocol API secrets → **Create**. Copy the secret.
- tagmanager.google.com → create a Web container. Copy the **Container ID** (`GTM-XXXXXXX`).
- In GTM: add a "Google tag" with your `G-` ID, fired on All Pages. Then add one GA4 Event tag per event in Phase 5 (Claude Code will write a `docs/GTM_SETUP.md` with the exact list). Click **Submit** to publish.

**4. (Optional) Sanity** — only if you have time after everything else works. Not needed for the Loom.

### Never share
Do not paste keys into the Loom, GitHub, or chat. They go only in `.env` files (already git-ignored) and in the Vercel/Render dashboards.

---

## PART B — Instructions for Claude Code

You are extending an existing, working app called **Trip Check** (`travel-disruption-checker`). Work fast and without asking questions: every product and technical decision is made below. If something is truly ambiguous, choose the simplest option that matches this file, note it in `docs/BUILD_LOG.md`, and continue.

### Ground rules
1. **Branch:** create `feature/accounts-paywall` from `main`. Commit after each phase with a clear message. Do not push unless the user asks.
2. **Small changes to existing files.** The owner is still learning and must be able to explain the code in an interview. Put new logic in **new files** and change existing files only where needed (an import, a dependency, a few lines). Do not refactor, rename or reformat existing code.
3. **Off by default.** If the Supabase/Stripe/GTM env vars are missing, the app must behave exactly as it does today: no login, no paywall, no tracking. This follows the repo's existing pattern ("without keys: rule-based summaries").
4. **Keep it working:** after every phase run `cd backend && python -m pytest -q` and `cd frontend && npm run typecheck`. Fix what you break. Don't change existing tests except to add new ones.
5. **Never commit secrets.** Add every new variable to `backend/.env.example` and a new `frontend/.env.example` with empty values and a one-line comment.
6. **Explain as you go.** After each phase, add a short section to `docs/BUILD_LOG.md` in plain, simple English: what you built, which files, and why. The owner will use this to present the work, so write it for a beginner.

### The codebase today (what you need to know)
- **Backend:** FastAPI in `backend/app/`. Entry point is `main.py`. Checks run through `POST /api/assess` and `POST /api/assess/stream` (NDJSON: a quick rules-only answer first, then the AI answer). `assess.py` → `assess_stream()` resolves the travel mode at about line 316: `mode = req.mode if req.mode != "auto" else ("drive" if dist < DRIVE_THRESHOLD_MILES else "fly")`. Config comes from env vars in `config.py` via python-dotenv. CORS comes from `CORS_ORIGINS`.
- **Frontend:** Next.js 15 (App Router) + React 19 + TypeScript in `frontend/`. A single client page, `app/page.tsx`. API helpers live in `lib/types.ts`. `assessTrip()` POSTs to `${NEXT_PUBLIC_STREAM_URL ?? ""}/api/assess/stream`. **In production the stream goes straight to the Render backend (cross-origin)**, and other calls go through the Next.js `/api` rewrite in `next.config.mjs`.
- **Deploy:** frontend on Vercel, backend on Render (its disk is wiped on redeploy), plus `docker-compose.yml` for local runs.
- **Consequence:** don't rely on cookies between frontend and backend (cross-site). Identify users with headers: `Authorization: Bearer <supabase access token>` and `X-Anon-Id: <uuid>`.

### Product decisions (final)
| Topic | Decision |
|---|---|
| Who is gated | **Air checks only** (resolved mode == `fly`, including `auto` that resolves to fly). Car checks stay free and unlimited, with no login. |
| Free allowance | **1 free air check** per visitor, with no account needed. |
| 2nd air check, anonymous | Blocked → paywall card: "You've used your free air check. Create a free account to continue." Buttons: Sign up / Sign in. |
| 2nd air check, signed in, not paying | Blocked → paywall card with the plan and an "Upgrade" button → Stripe Checkout. |
| Paid users | Unlimited air checks. |
| Plan | **Trip Check Pro, $4.99 per month**, Stripe **subscription** mode, test mode only. |
| Counting | Count a free check only when its **first answer was produced successfully**. Errors and blocked attempts don't count. |
| Anti-abuse | A free air check is used if **either** the anon ID **or** the hashed IP has already used one. |
| Login methods | Email + password (required). "Sign in with Google" button shown only if `NEXT_PUBLIC_ENABLE_GOOGLE_AUTH=true`. |
| Billing management | Stripe Customer Portal link on an Account page. |
| Database | **Supabase PostgreSQL** (+ Supabase Auth). Backend talks to Postgres with `asyncpg` over the session pooler URL. |
| Analytics | GTM in the Next.js layout + dataLayer events. `purchase` is also sent **server-side** via the GA4 Measurement Protocol from the Stripe webhook. |
| CMS | Optional Phase 6 (Sanity) for pricing-page text, with hard-coded fallback content. |

### Environment variables
**backend/.env**
```
DATABASE_URL=            # Supabase session pooler URI (postgresql://...)
SUPABASE_URL=            # https://<ref>.supabase.co  (used for JWKS)
SUPABASE_JWT_SECRET=     # optional: legacy HS256 secret; if empty, verify with JWKS
IP_HASH_SALT=            # any long random string
STRIPE_SECRET_KEY=       # sk_test_...
STRIPE_WEBHOOK_SECRET=   # whsec_... (from `stripe listen` locally, or the dashboard endpoint)
STRIPE_PRICE_ID=         # price_... (printed by scripts/stripe_setup.py)
FRONTEND_URL=http://localhost:3000   # for Checkout success/cancel and portal return URLs
GA4_MEASUREMENT_ID=      # G-...
GA4_API_SECRET=          # Measurement Protocol secret
```
**frontend/.env.local**
```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=     # publishable/anon key
NEXT_PUBLIC_GTM_ID=                # GTM-...
NEXT_PUBLIC_ENABLE_GOOGLE_AUTH=false
NEXT_PUBLIC_STREAM_URL=            # leave empty locally
```
Feature flags: the **paywall is on** only when `DATABASE_URL` and `SUPABASE_URL` are set. **Billing is on** only when the Stripe vars are also set. **Tracking is on** only when `NEXT_PUBLIC_GTM_ID` is set.

---

### Phase 0 — Setup
- Create the branch.
- Backend: add to `requirements.txt` `asyncpg`, `PyJWT[crypto]`, `stripe`. Keep the existing style (`>=` lower bounds).
- Frontend: `npm install @supabase/supabase-js @next/third-parties`.
- Create `docs/BUILD_LOG.md` and `frontend/.env.example`.

### Phase 1 — Database (Supabase Postgres)
Create `supabase/migrations/001_accounts_paywall.sql` and `docs/DB_SETUP.md` ("paste this into Supabase → SQL Editor → Run").

```sql
-- one row per user, created automatically on sign-up
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  stripe_customer_id text unique,
  plan text not null default 'free',              -- 'free' | 'pro'
  subscription_status text,                       -- Stripe status: active, trialing, past_due, canceled...
  current_period_end timestamptz,
  created_at timestamptz not null default now()
);

-- every check that ran (used for limits and for the founder's dashboard)
create table public.checks (
  id bigserial primary key,
  user_id uuid references auth.users(id) on delete set null,
  anon_id uuid,
  ip_hash text,
  mode text not null,                             -- 'fly' | 'drive'
  origin text not null,
  destination text not null,
  trip_date date not null,
  created_at timestamptz not null default now()
);
create index on public.checks (anon_id) where mode = 'fly';
create index on public.checks (ip_hash) where mode = 'fly';
create index on public.checks (user_id);

-- Stripe webhook idempotency: each event is processed once
create table public.stripe_events (
  id text primary key,
  type text not null,
  received_at timestamptz not null default now()
);

-- auto-create a profile when a user signs up
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email) on conflict do nothing;
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Row Level Security: users may read only their own profile. The backend uses the DB owner role.
alter table public.profiles enable row level security;
alter table public.checks enable row level security;
alter table public.stripe_events enable row level security;
create policy "read own profile" on public.profiles for select using (auth.uid() = id);
```

New file `backend/app/db.py`:
- An `asyncpg` pool created in the existing `lifespan()` in `main.py` (add 2–3 lines; leave the warm-up logic alone) and closed on shutdown.
- Pass `statement_cache_size=0` so it works with Supabase poolers.
- `enabled()` returns False when `DATABASE_URL` is empty. Every function in this module is a no-op/permissive when disabled.
- Add the new env vars to `config.py`, following the existing style.

### Phase 2 — Sign-up and sign-in
**Backend** — new `backend/app/auth.py`:
- `async def current_user(request) -> dict | None`: reads `Authorization: Bearer <jwt>`. Verifies it with PyJWT: HS256 with `SUPABASE_JWT_SECRET` if set, otherwise `PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")` (cache the client). Checks `aud == "authenticated"`. Returns `{"id": sub, "email": email}` or None. A missing or invalid token returns None and never raises (anonymous use must keep working).
- `def anon_id(request) -> uuid | None`: reads and validates the `X-Anon-Id` header.
- `def ip_hash(request) -> str`: the first IP in `X-Forwarded-For`, else `request.client.host`, hashed with sha256 + `IP_HASH_SALT`. Never store raw IPs.

**Frontend:**
- `lib/supabase.ts`: a browser client from the `NEXT_PUBLIC_` vars, or `null` when unset.
- `lib/anon.ts`: `getAnonId()` returns a UUID kept in `localStorage` (`tc_anon_id`), created with `crypto.randomUUID()`.
- `lib/session.ts`: a small `useSession()` hook (subscribes to `onAuthStateChange`) and `authHeaders()` returning `{Authorization, X-Anon-Id}` as available.
- New pages: `app/signin/page.tsx`, `app/signup/page.tsx` (email + password, plus the optional Google button), `app/account/page.tsx` (email, plan, renewal date, "Manage billing", "Sign out").
- Plain styling that reuses classes in `globals.css`. Accessible: labelled inputs, `role="alert"` errors, keyboard friendly.
- Header: add a tiny `components/AccountLink.tsx` rendered in `page.tsx`'s masthead: "Sign in" or the user's email linking to `/account`. Render nothing when Supabase isn't configured.
- After sign-up/sign-in, redirect back to `/` (or `/?upgrade=1` if they came from the paywall, which opens the paywall in "upgrade" mode).

### Phase 3 — The free air-check gate (backend-enforced)
New `backend/app/gate.py`:
- `async def decide(user, anon_id, ip_hash, mode) -> Decision`:
  - Paywall disabled, or `mode != "fly"` → allow.
  - Signed-in user with `subscription_status in ('active','trialing')` and `current_period_end > now()` → allow.
  - Has an unused free check → allow, marked `uses_free=True`. **Anonymous:** none of its anon_id or ip_hash appear in `checks` with mode `fly`. **Signed in:** the user has no `fly` row, and their current anon_id/ip_hash haven't used one either. Signing up does **not** grant an extra free check.
  - Otherwise → block with code `free_limit_reached` (anonymous) or `subscription_required` (signed in).
- `async def record(user, anon_id, ip_hash, mode, req)`: inserts a `checks` row (for drive checks too, for analytics).

**Mode resolution:** the gate needs the *resolved* mode. Reuse the logic at `assess.py` ~line 316 without paying for geocoding twice. Preferred, smallest change: add an optional `on_mode: Callable[[str], Awaitable[None]] | None = None` parameter to `assess_stream()` (and pass it through `assess()`), called right after `mode` is resolved and **before** any data sources are fetched. In `main.py`, pass a callback that runs `gate.decide()` and raises a custom `PaywallBlocked` exception when blocked. Existing callers and tests that don't pass `on_mode` must behave exactly as before.

**In `main.py`** (both `/api/assess` and `/api/assess/stream`):
- Get `user`, `anon_id`, `ip_hash` from `auth.py`.
- Catch `PaywallBlocked` and return **HTTP 402**:
  `{"detail": {"code": "free_limit_reached" | "subscription_required", "message": "<plain sentence>", "signed_in": bool}}`.
- In the stream endpoint, call `gate.record()` only after `first` has been produced (the existing `first = await answers.__anext__()`). In `/api/assess`, call it after `assess()` returns.
- Add `X-Anon-Id` and `Authorization` to CORS if needed. `allow_headers=["*"]` is already set, so check that it works cross-origin.

**Frontend:**
- In `lib/types.ts` `assessTrip()`: send `...authHeaders()` with the request. In the `!res.ok` block, add a small branch: when `res.status === 402`, throw `new PaywallError(detail.code, detail.message)`, a new exported class. Leave the rest of the function unchanged.
- New `components/Paywall.tsx`: a card shown in place of the error. Anonymous → "Create free account" / "Sign in". Signed in → plan name, $4.99/month, 3 benefit bullets, an "Upgrade" button (Phase 4).
- In `app/page.tsx`: in `run()`'s catch, store the `PaywallError` in a new `paywall` state and render `<Paywall>`. Keep the edit to about 10 lines.

### Phase 4 — Stripe subscriptions (webhook is the source of truth)
- `backend/scripts/stripe_setup.py`: idempotently creates the product "Trip Check Pro" and a monthly $4.99 USD price with `lookup_key="tripcheck_pro_monthly"` in **test mode**, then prints the price ID to put in `STRIPE_PRICE_ID`. It refuses to run with a live key (`sk_live_`).
- New `backend/app/billing.py` with routes mounted from `main.py` via an `APIRouter` (one `include_router` line):
  - `POST /api/billing/checkout` (requires a signed-in user, otherwise 401): get or create a Stripe Customer (store `stripe_customer_id`), create a Checkout Session with `mode="subscription"`, `line_items=[{price: STRIPE_PRICE_ID, quantity: 1}]`, `client_reference_id=user_id`, `metadata={"user_id":..., "ga_client_id": <from request body, optional>}`, `subscription_data.metadata.user_id`, `allow_promotion_codes=True`, `success_url=f"{FRONTEND_URL}/account?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"`, `cancel_url=f"{FRONTEND_URL}/?checkout=cancelled"`. Returns `{url}`. The frontend redirects with `window.location.href = url` (no Stripe.js needed).
  - `POST /api/billing/portal` (signed in): creates a Customer Portal session and returns `{url}`.
  - `POST /api/stripe/webhook`: reads the **raw body** and verifies it with `stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)` (400 on failure). Insert into `stripe_events`. If the ID already exists, return 200 and do nothing (idempotent). Handle:
    - `checkout.session.completed` → retrieve the subscription, update the profile (plan `pro`, status, `current_period_end`), then send the server-side GA4 `purchase` (Phase 5).
    - `customer.subscription.updated` / `customer.subscription.deleted` → update status, period end, and plan (`free` when canceled/unpaid).
    - `invoice.payment_failed` → set status `past_due`.
  - Read `current_period_end` defensively: newer Stripe API versions put it on the subscription item (`items.data[0].current_period_end`). Support both.
  - **Never activate a subscription from the success redirect.** The account page just shows status. If it isn't active yet, show "Confirming your payment…" and re-fetch `GET /api/me` every 2 s for up to 20 s.
- `GET /api/me` (signed in): returns `{email, plan, subscription_status, current_period_end, free_air_check_used}`.
- Frontend: the Paywall "Upgrade" button → `POST /api/billing/checkout` with auth headers → redirect. Account page "Manage billing" → portal.
- `docs/STRIPE_TESTING.md`: `stripe listen --forward-to localhost:8000/api/stripe/webhook`, test card `4242 4242 4242 4242`, the declined card `4000 0000 0000 0002`, and how to add the Render webhook endpoint in the dashboard with the 4 events above.
- Note in the docs: billing calls go to the backend through the existing `/api` rewrite, but Stripe must call the webhook on the **Render URL directly**.

### Phase 5 — GA4 + Google Tag Manager
- In `app/layout.tsx`: when `NEXT_PUBLIC_GTM_ID` is set, render `<GoogleTagManager gtmId=... />` from `@next/third-parties/google` (it loads after hydration, so it doesn't hurt LCP). Add only these lines and keep the existing font links.
- New `lib/analytics.ts`: `track(event, params)` wraps `sendGTMEvent({event, ...params})` and is a no-op when GTM is off. **Never send PII** (no emails, no full addresses): send city-level `origin`/`destination` only.
- Events and where they fire:
  | Event | Where | Params |
  |---|---|---|
  | `check_started` | `page.tsx` `run()` start | `mode`, `has_flights` |
  | `check_completed` | first answer received | `mode`, `risk_level`, `ms_to_first_answer` |
  | `paywall_shown` | Paywall mounts | `reason` (code) |
  | `sign_up` | after successful sign-up | `method` |
  | `login` | after sign-in | `method` |
  | `begin_checkout` | Upgrade click | `value: 4.99`, `currency: "USD"`, `items` |
  | `purchase` | **server-side** from the webhook | `transaction_id` (Stripe invoice/session id), `value`, `currency` |
- Server-side purchase: `billing.py` POSTs to `https://www.google-analytics.com/mp/collect?measurement_id=...&api_secret=...` with the `client_id` the frontend read from the `_ga` cookie (sent as `ga_client_id` at checkout; fall back to a random ID). Fire and forget, with a 3 s timeout and errors logged, never raised.
- `docs/GTM_SETUP.md`: step-by-step clicks to create the Google tag, a Custom Event trigger + GA4 Event tag for each event above (with which dataLayer variables to map), and how to verify in GTM Preview and GA4 DebugView. Also how to build the funnel exploration: check_started → paywall_shown → sign_up → begin_checkout → purchase.

### Phase 6 — (Optional, only if Phases 0–5 are done) Sanity headless CMS for pricing copy
- Skip this phase if `NEXT_PUBLIC_SANITY_PROJECT_ID` isn't set. The Paywall then uses hard-coded copy.
- `sanity/schemas/pricingPlan.ts`: `name`, `priceLabel`, `benefits[]`, `ctaLabel`, `faq[]` (question/answer).
- `lib/cms.ts`: fetch the published `pricingPlan` via the Sanity HTTP API (GROQ) with Next.js `revalidate: 300`, falling back to the defaults on any error.
- Document in `docs/CMS.md`: why headless (marketing can change prices and wording without a deploy) and how the content model maps to the UI.

### Phase 7 — Tests, docs, finish
- New pytest files (no network, no real DB: fake the db/gate functions with monkeypatch):
  - `tests/test_gate.py`: drive is always allowed. First anonymous fly is allowed with `uses_free`. A second fly by the same anon ID is blocked with `free_limit_reached`. The same IP with a new anon ID is blocked. A signed-in free user who has used it gets `subscription_required`. Active pro is allowed. An expired pro is blocked. Paywall disabled → always allowed.
  - `tests/test_billing_webhook.py`: a bad signature → 400. A valid signed event (build the header with the test secret, the way `stripe.WebhookSignature` expects) → profile update called. The same event ID twice → processed once.
  - `tests/test_auth.py`: no/garbage token → None. A valid HS256 token → the user.
- All existing tests still pass. `npm run typecheck` and `npm run build` pass.
- Update `README.md` with a short "Accounts and payments" section: what it does, how to switch it on, and links to the new docs. Leave the rest of the README alone.
- Finish `docs/BUILD_LOG.md` with: (1) a one-paragraph summary a founder would understand, (2) a simple ASCII architecture diagram (browser → Vercel/Next.js → Render/FastAPI → Supabase Postgres; Stripe → webhook → FastAPI; GTM → GA4), (3) the 3 most important technical decisions and their trade-offs: backend-enforced gate vs frontend-only; webhook vs success-URL activation; headers vs cookies because of cross-origin streaming.

### Done checklist (verify each, then report back)
- [ ] With no new env vars set: the app works exactly as before (no login link, no paywall).
- [ ] Anonymous: the 1st air check works. The 2nd shows the paywall (HTTP 402). Car checks always work.
- [ ] Clearing localStorage doesn't give a new free air check from the same IP.
- [ ] Sign up → still blocked, with "subscription_required" → Upgrade → Stripe test checkout with 4242… → the webhook sets `pro` → air checks unlimited.
- [ ] Opening `/account?checkout=success` by hand does **not** activate anything.
- [ ] Customer Portal → cancel → the webhook sets the status. After the period ends, air checks are blocked again.
- [ ] GTM Preview shows every event in the table. GA4 DebugView shows `purchase` from the server.
- [ ] `pytest` passes, and `npm run typecheck` and `npm run build` pass.
- [ ] No secrets in git (`git grep -nE "sk_test_|sk_live_|whsec_|service_role"` returns nothing).

When everything is checked, print a short summary of what changed, which env vars the owner must set on **Vercel** and on **Render**, and the exact Stripe webhook URL to register.
