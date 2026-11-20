"use client";

// The masthead's "Sign in" link, or the signed-in user's email linking to their account.
// Renders nothing when accounts aren't configured.
import { useEffect } from "react";
import { track } from "@/lib/analytics";
import { useSession } from "@/lib/session";
import { accountsEnabled } from "@/lib/supabase";

export function AccountLink() {
  const { session, ready } = useSession();

  // a password-reset link that Supabase sent to the home page (when /reset-password isn't in its
  // allowed redirect URLs): pass it on, with its token, to the page that sets the new password
  useEffect(() => {
    if (window.location.hash.includes("type=recovery")) window.location.replace(`/reset-password${window.location.hash}`);
  }, []);

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

  if (!accountsEnabled || !ready) return null;
  return (
    <nav className="account-link" aria-label="Account">
      {session ? <a href="/account">{session.user.email ?? "Your account"}</a> : <a href="/signin">Sign in</a>}
    </nav>
  );
}
