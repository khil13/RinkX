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
