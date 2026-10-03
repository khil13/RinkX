import { ago, localTime } from "../lib/format";

/** "Updated 2 minutes ago · source", and a plain warning once data is older than it should be. */
export function Freshness({ at, staleAfterMin, source }: { at: string | null | undefined; staleAfterMin: number; source?: string }) {
  if (!at) return <p className="text-[11px] text-warn">⚠️ DATA UNAVAILABLE: no timestamp for this data.</p>;
  const minutes = (Date.now() - Date.parse(at)) / 60000;
  const stale = minutes > staleAfterMin;
  return (
    <p className={`text-[11px] ${stale ? "font-semibold text-warn" : "text-muted"}`} title={localTime(at)} aria-label="Data freshness">
      {stale && "⚠️ DATA MAY BE STALE · "}Updated {ago(at)}
      {source && ` · ${source}`}
    </p>
  );
}
