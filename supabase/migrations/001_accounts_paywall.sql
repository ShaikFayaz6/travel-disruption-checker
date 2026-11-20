-- Trip Check: accounts, the free air-check limit and Stripe subscriptions.
-- Paste into Supabase -> SQL Editor -> Run. Safe to run more than once.

-- one row per user, created automatically on sign-up
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  stripe_customer_id text unique,
  plan text not null default 'free',              -- 'free' | 'pro'
  subscription_status text,                       -- Stripe status: active, trialing, past_due, canceled...
  current_period_end timestamptz,
  created_at timestamptz not null default now()
);

-- every check that ran (used for limits and for the founder's dashboard)
create table if not exists public.checks (
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
create index if not exists checks_anon_fly_idx on public.checks (anon_id) where mode = 'fly';
create index if not exists checks_ip_fly_idx on public.checks (ip_hash) where mode = 'fly';
create index if not exists checks_user_idx on public.checks (user_id);

-- Stripe webhook idempotency: each event is processed once
create table if not exists public.stripe_events (
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
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- profiles for users who signed up before this migration
insert into public.profiles (id, email) select id, email from auth.users on conflict do nothing;

-- Row Level Security: users may read only their own profile. The backend uses the DB owner role.
alter table public.profiles enable row level security;
alter table public.checks enable row level security;
alter table public.stripe_events enable row level security;
drop policy if exists "read own profile" on public.profiles;
create policy "read own profile" on public.profiles for select using (auth.uid() = id);
