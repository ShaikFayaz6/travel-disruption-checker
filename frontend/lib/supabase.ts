// The Supabase browser client (sign-up, sign-in, sessions), or null when Supabase isn't configured.
// Null means accounts are off: no sign-in link, and the app works exactly as it did before.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY; // the public anon/publishable key, never service_role

export const supabase: SupabaseClient | null = url && key ? createClient(url, key) : null;

export const googleAuthEnabled = process.env.NEXT_PUBLIC_ENABLE_GOOGLE_AUTH === "true";
