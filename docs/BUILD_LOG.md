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
