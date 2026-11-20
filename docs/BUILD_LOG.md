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
