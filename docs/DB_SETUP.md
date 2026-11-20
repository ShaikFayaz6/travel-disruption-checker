# Database setup (Supabase)

Trip Check stores accounts, checks and Stripe events in Supabase's PostgreSQL database.

## 1. Create the tables

1. Open your project on supabase.com.
2. Go to **SQL Editor** → **New query**.
3. Paste everything in [`supabase/migrations/001_accounts_paywall.sql`](../supabase/migrations/001_accounts_paywall.sql).
4. Click **Run**. You should see "Success. No rows returned".

It is safe to run again: every statement checks whether its table, index or policy already exists.

**Or from your computer**, once `DATABASE_URL` is set (step 3):

```bash
cd backend
python scripts/db_migrate.py
```

## 2. What it creates

| Table | One row per | Used for |
|---|---|---|
| `profiles` | user (made automatically on sign-up by a trigger) | plan (`free`/`pro`), Stripe customer and subscription status |
| `checks` | check that produced an answer | the 1-free-air-check limit, and usage numbers |
| `stripe_events` | Stripe webhook event | processing each event only once |

Visitors' IP addresses are never stored, only a salted SHA-256 hash (`ip_hash`).

Row Level Security is on for all three tables. Browsers (with the public anon key) may read only
their own profile. The backend connects as the database owner, so it is not limited by these rules.

## 3. Connect the backend

1. In Supabase, click **Connect** (top bar) → **Session pooler**. (Not "Direct connection":
   it is IPv6-only, which Render can't reach.)
2. Copy the URI and replace `[YOUR-PASSWORD]` with your database password.
3. Put it in `backend/.env` as `DATABASE_URL=...`, and set `SUPABASE_URL=https://<ref>.supabase.co`.
4. Restart the backend. The paywall is now on.

## Useful queries

```sql
-- air checks per day
select date(created_at), count(*) from checks where mode = 'fly' group by 1 order by 1 desc;
-- who is paying
select email, plan, subscription_status, current_period_end from profiles where plan = 'pro';
```
