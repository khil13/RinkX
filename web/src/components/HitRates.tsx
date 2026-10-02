import { useState } from "react";
import type { HitRateStat, WindowName } from "../lib/data/types";
import { longDate, wilson } from "../lib/format";

const COLUMNS: [WindowName, string][] = [
  ["L5", "L5"],
  ["L10", "L10"],
  ["L15", "L15"],
  ["L20", "L20"],
  ["season", "Season"],
  ["last_season", "Last szn"],
];

function Cell({ hits, n }: { hits: number; n: number }) {
  if (n === 0) return <span className="text-muted">—</span>;
  const pct = Math.round((hits / n) * 100);
  const ci = wilson(hits, n);
  const title = ci ? `${pct}% · 95% range ${Math.round(ci[0] * 100)}–${Math.round(ci[1] * 100)}%` : undefined;
  return (
    <span title={title} className="flex flex-col items-end leading-tight">
      <span>
        {hits}/{n}
      </span>
      <span className="text-[10px] text-muted">{pct}%</span>
    </span>
  );
}

/** Threshold x window hit counts. Shows counts for every threshold: no line is assumed. */
export function HitRates({ rates, basis }: { rates: Record<string, HitRateStat>; basis: "games_played" | "starts" }) {
  const keys = Object.keys(rates);
  const [key, setKey] = useState(keys[0] ?? "");
  const stat = rates[key];
  if (!stat) return null;
  const l20 = stat.windows.L20;
  const anyMissing = COLUMNS.some(([w]) => stat.windows[w].missing > 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="-mx-1 flex gap-1 overflow-x-auto pb-1" role="tablist" aria-label="Stat">
        {keys.map((k) => (
          <button
            key={k}
            role="tab"
            aria-selected={k === key}
            onClick={() => setKey(k)}
            className={`min-h-9 shrink-0 rounded-md px-2.5 text-xs ${
              k === key ? "bg-accent text-bg" : "border border-line text-muted hover:text-text"
            }`}
          >
            {rates[k]!.label}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table className="num w-full min-w-[360px] text-sm" aria-label={`${stat.label} hit rates`}>
          <thead className="text-xs text-muted">
            <tr>
              <th className="sticky left-0 bg-panel py-1 pr-1 text-left font-normal" aria-label="At least">≥</th>
              {COLUMNS.map(([w, label]) => (
                <th key={w} className="py-1 text-right font-normal">
                  {label}
                  <div className="text-[10px]">{stat.windows[w].known} gp</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stat.thresholds.map((k, i) => (
              <tr key={k} className="border-t border-line">
                <td className="sticky left-0 bg-panel py-1.5 pr-2">{k}+</td>
                {COLUMNS.map(([w]) => (
                  <td key={w} className="py-1.5 text-right">
                    <Cell hits={stat.windows[w].counts[i]!} n={stat.windows[w].known} />
                  </td>
                ))}
              </tr>
            ))}
            {(["mean", "median", "sd"] as const).map((m) => (
              <tr key={m} className="border-t border-line text-muted">
                <td className="sticky left-0 bg-panel py-1.5 pr-2 font-sans text-xs">
                  {m === "sd" ? "Std dev" : m === "mean" ? "Average" : "Median"}
                </td>
                {COLUMNS.map(([w]) => (
                  <td key={w} className="py-1.5 text-right text-xs">
                    {stat.windows[w][m] ?? "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="flex flex-col gap-1 text-xs text-muted">
        <li>
          Counts {basis === "starts" ? "starts only" : "games played"}, most recent first. Missed games are not
          counted. Every game in a window counts; nothing is filtered.
        </li>
        {l20.from && l20.to && (
          <li>
            L-windows can reach into last season: L20 covers {longDate(l20.from)} – {longDate(l20.to)}.
          </li>
        )}
        {anyMissing && <li>Some games are missing this stat (not loaded yet) and are left out, not counted as 0.</li>}
        <li>Hover or long-press a cell for its 95% range; small samples like 4/5 are uncertain.</li>
      </ul>
    </div>
  );
}
