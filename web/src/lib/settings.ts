// Display settings, stored on this device only (localStorage). Nothing here is sent anywhere.
import { useSyncExternalStore } from "react";

export interface Settings {
  odds: "american" | "decimal";
  /** IANA zone, or "" for the device's own */
  timeZone: "" | "America/New_York" | "America/Chicago" | "America/Denver" | "America/Los_Angeles" | "UTC";
  /** ISO time; prices and props stay hidden on this device until then */
  coolOffUntil: string | null;
}

export const DEFAULT_SETTINGS: Settings = { odds: "american", timeZone: "", coolOffUntil: null };
const KEY = "rinkx.settings";
const listeners = new Set<() => void>();
let cache: Settings | null = null;

export function getSettings(): Settings {
  if (cache) return cache;
  try {
    cache = { ...DEFAULT_SETTINGS, ...(JSON.parse(localStorage.getItem(KEY) ?? "{}") as Partial<Settings>) };
  } catch {
    cache = DEFAULT_SETTINGS;
  }
  return cache;
}

export function updateSettings(patch: Partial<Settings>) {
  const next = { ...getSettings(), ...patch };
  // A cool-off can only be extended, never shortened, from the app.
  const cur = getSettings().coolOffUntil;
  if (cur && Date.parse(cur) > Date.now() && (!next.coolOffUntil || Date.parse(next.coolOffUntil) < Date.parse(cur))) {
    next.coolOffUntil = cur;
  }
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage blocked: settings last for this visit */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useSettings(): Settings {
  return useSyncExternalStore(subscribe, getSettings, () => DEFAULT_SETTINGS);
}

export function coolOffActive(s: Settings = getSettings(), now = Date.now()): boolean {
  return s.coolOffUntil !== null && Date.parse(s.coolOffUntil) > now;
}

/** A sportsbook price in the chosen format. */
export function odds(p: number | null | undefined, fmt: Settings["odds"] = getSettings().odds): string {
  if (p === null || p === undefined) return "—";
  if (fmt === "decimal") return (1 + (p > 0 ? p / 100 : 100 / -p)).toFixed(2);
  return p > 0 ? `+${p}` : `−${Math.abs(p)}`;
}

/** Time-zone option for toLocale* calls (undefined = the device's zone). */
export function tz(): string | undefined {
  return getSettings().timeZone || undefined;
}
