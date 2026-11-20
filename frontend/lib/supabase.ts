// The Supabase browser client (sign-up, sign-in, sessions). accountsEnabled is false when the
// NEXT_PUBLIC_SUPABASE_* settings are empty: no sign-in link, and the app works exactly as before.
// The library (~60 kB) is loaded on demand, so it never delays the first paint of the check page.
import type { SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY; // the public anon/publishable key, never service_role

export const accountsEnabled = Boolean(url && key);
export const googleAuthEnabled = process.env.NEXT_PUBLIC_ENABLE_GOOGLE_AUTH === "true";

let client: Promise<SupabaseClient> | null = null;

export function getSupabase(): Promise<SupabaseClient | null> {
  if (!accountsEnabled || typeof window === "undefined") return Promise.resolve(null);
  client ??= import("@supabase/supabase-js").then(({ createClient }) => createClient(url!, key!));
  return client;
}
