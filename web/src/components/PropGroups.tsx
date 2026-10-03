import { useState } from "react";
import type { PropRow } from "../lib/data/types";
import { hitRate, SORT_LABELS, SORTS, type SortKey } from "../lib/propFilters";
import { betText, Drawer } from "../routes/BestProps";
import { odds, pts } from "./Lines";
import { pct } from "./Projections";
import { ScoreBadge } from "./Scores";
import { TeamChip } from "./Team";

export { GROUPS } from "../lib/marketGroups";
import { GROUPS } from "../lib/marketGroups";

/** Best book per prop (same subject, market, line, side), then sorted. */
export function bestPerProp(rows: PropRow[]): PropRow[] {
  const best = new Map<string, PropRow>();
  for (const r of rows) {
    const key = `${r.game.id}|${r.subject.name}|${r.market}|${r.line}|${r.lean ?? r.side_scored}`;
    const cur = best.get(key);
    if (!cur || (r.ev ?? -9) > (cur.ev ?? -9)) best.set(key, r);
  }
  return [...best.values()];
}

export function PropLine({ r, onOpen }: { r: PropRow; onOpen: () => void }) {
  const h = hitRate(r);
  return (
    <li>
      <button type="button" onClick={onOpen} className="flex w-full flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1.5 text-left text-sm hover:text-accent">
        <span className="flex flex-wrap items-center gap-1.5">
          {r.subject.team && <TeamChip abbrev={r.subject.team} />}
          <span className="font-semibold">{r.subject.type === "player" ? r.subject.name : ""}</span>
          <span>{r.lean ? betText(r) : <span className="text-muted">no lean · {betText(r)}</span>}</span>
          <span className="num font-semibold">{odds(r.price)}</span>
          <ScoreBadge score={r.scores?.intelligence} label="Prop Intelligence" />
        </span>
        <span className="num text-[11px] text-muted">
          model {pct(r.p_model)} · edge {pts(r.edge)} · EV {r.ev != null ? r.ev.toFixed(2) : "—"}
          {r.projection != null && ` · proj ${r.projection.toFixed(2)}`}
          {h !== null && ` · L10 ${Math.round(h * 100)}%`}
        </span>
      </button>
    </li>
  );
}

/** One section per market group, each with its best few props, sortable. */
export function PropGroups({ rows, perGroup = 3, label }: { rows: PropRow[]; perGroup?: number; label: string }) {
  const [sort, setSort] = useState<SortKey>("intelligence");
  const [open, setOpen] = useState<PropRow | null>(null);
  const pool = bestPerProp(rows);
  const empty = GROUPS.filter((g) => !pool.some((r) => g.markets.includes(r.market))).map((g) =>
    g.title.replace("🔥 Best ", "").replace(" props", ""),
  );
  return (
    <div className="flex flex-col gap-3" aria-label={label}>
      <label className="flex items-center gap-2 self-end text-[11px] text-muted">
        Sort by
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className="min-h-9 rounded border border-line bg-panel px-2 text-sm text-text">
          {(["intelligence", "edge", "projection", "hit", "odds", "ev"] as SortKey[]).map((k) => (
            <option key={k} value={k}>
              {SORT_LABELS[k]}
            </option>
          ))}
        </select>
      </label>
      {GROUPS.map((g) => ({ g, items: pool.filter((r) => g.markets.includes(r.market)).sort(SORTS[sort]).slice(0, perGroup) }))
        .filter((x) => x.items.length > 0)
        .map(({ g, items }) => (
          <section key={g.key} aria-label={g.title.replace("🔥 ", "")}>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{g.title}</h3>
            <ul className="divide-y divide-line">
              {items.map((r) => (
                <PropLine key={r.prediction_id} r={r} onOpen={() => setOpen(r)} />
              ))}
            </ul>
          </section>
        ))}
      {empty.length > 0 && (
        <p className="text-xs text-muted" aria-label="Markets without priced lines">
          No priced lines yet: {empty.join(", ")}.
        </p>
      )}
      {open && <Drawer r={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
