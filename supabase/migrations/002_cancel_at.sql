-- When a cancelled subscription stops (Stripe's cancel_at). Null = it renews.
-- The plan stays Pro until then; the account page shows "Ends on" instead of "Renews on".
alter table public.profiles add column if not exists cancel_at timestamptz;
