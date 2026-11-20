// Analytics events for Google Tag Manager (dataLayer). A no-op when NEXT_PUBLIC_GTM_ID isn't set.
// Never send personal data: no emails, no full addresses. Cities only.
import { sendGTMEvent } from "@next/third-parties/google";

export const gtmId = process.env.NEXT_PUBLIC_GTM_ID || "";

export function track(event: string, params: Record<string, unknown> = {}): void {
  if (!gtmId || typeof window === "undefined") return;
  try {
    sendGTMEvent({ event, ...params });
  } catch {
    // tracking must never break the page
  }
}

// GA4's client ID from its _ga cookie ("GA1.1.<id>.<timestamp>" -> "<id>.<timestamp>"), so the
// server-side purchase event joins the same visitor's journey in GA4
export function gaClientId(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.match(/(?:^|;\s*)_ga=GA\d+\.\d+\.(\d+\.\d+)/);
  return m?.[1];
}
