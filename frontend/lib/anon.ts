// A random ID for this browser, kept in localStorage. It lets the backend count the one free air check
// for visitors who haven't signed up. (The backend also checks a hashed IP, so clearing it doesn't help.)
const KEY = "tc_anon_id";

export function getAnonId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return null; // storage blocked (private window): the IP hash still counts the free check
  }
}
