export function minutesSince(iso: string, now: number = Date.now()): number {
  return Math.max(0, Math.floor((now - Date.parse(iso)) / 60_000));
}

export function ago(iso: string, now: number = Date.now()): string {
  const m = minutesSince(iso, now);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

export function localTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** The whole site counts as stale if the pipeline hasn't published for this long. */
export const SITE_STALE_MINUTES = 90;

/** Owner/repo for links back to GitHub, derived from the Pages URL (owner.github.io/repo/). */
export function githubRepo(loc: Location = window.location): { owner: string; repo: string } | null {
  const m = loc.hostname.match(/^([^.]+)\.github\.io$/);
  const repo = loc.pathname.split("/").filter(Boolean)[0];
  return m?.[1] && repo ? { owner: m[1], repo } : null;
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
}

export function longDate(day: string): string {
  // `day` is a calendar date (YYYY-MM-DD); format it without shifting through UTC.
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function mmss(seconds: number | null): string {
  if (seconds === null) return "—";
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function signed(n: number | null): string {
  if (n === null) return "—";
  return n > 0 ? `+${n}` : String(n);
}

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : String(n);
}

/** 95% Wilson score interval, matching pipeline/rinkx/analytics/hitrates.py. */
export function wilson(hits: number, n: number, z = 1.96): [number, number] | null {
  if (n === 0) return null;
  const p = hits / n;
  const denom = 1 + (z * z) / n;
  const centre = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return [Math.max(0, centre - half), Math.min(1, centre + half)];
}

export function seasonLabel(season: number | null): string {
  if (!season) return "";
  const s = String(season);
  return `${s.slice(0, 4)}-${s.slice(6)}`;
}
