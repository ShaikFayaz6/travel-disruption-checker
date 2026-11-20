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
