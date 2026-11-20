"use client";

// The masthead's "Sign in" link, or the signed-in user's email linking to their account.
// Renders nothing when accounts aren't configured.
import { useEffect } from "react";
import { track } from "@/lib/analytics";
import { useSession } from "@/lib/session";
import { supabase } from "@/lib/supabase";

export function AccountLink() {
  const { session, ready } = useSession();

  // back from "Sign in with Google": count the sign-up/login once
  useEffect(() => {
    if (!session) return;
    try {
      const kind = sessionStorage.getItem("tc_oauth");
      if (!kind) return;
      sessionStorage.removeItem("tc_oauth");
      track(kind === "signup" ? "sign_up" : "login", { method: "google" });
    } catch {}
  }, [session]);

  if (!supabase || !ready) return null;
  return (
    <nav className="account-link" aria-label="Account">
      {session ? <a href="/account">{session.user.email ?? "Your account"}</a> : <a href="/signin">Sign in</a>}
    </nav>
  );
}
