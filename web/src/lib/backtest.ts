// Backtest, CLV and "my bets" arithmetic over backtest.json (graded, pre-game frozen prices).
// Filters use only values frozen before puck drop, so a filter can't look ahead; results, profit
// and closing prices are what's measured.
import { cents } from "../components/Lines";

export interface BtRow {
  id: number;
  date: string;
  game: number;
  market: string;
  label: string;
  player: string | null;
  player_id: number | null;
  book: string | null;
  line: number | null;
  side: string;
  lean: number;
  price: number | null;
  p: number;
  p_mkt: number | null;
  edge: number | null;
  ev: number | null;
  conf: number | null;
  pi: number | null;
  result: "win" | "loss" | "push" | "void";
  profit: number | null;
  close: number | null;
  clv: number | null;
  version: string | null;
  synthetic: number;
}

export interface BacktestFile {
  generated_at: string;
  since: string;
  fields: (keyof BtRow)[];
  rows: unknown[][];
  about: string;
}

export function parse(f: BacktestFile): BtRow[] {
  return f.rows.map((r) => Object.fromEntries(f.fields.map((k, i) => [k, r[i]])) as unknown as BtRow);
}

export interface BtFilter {
  market: string;
  line: string;
  minProb: number; // %
  minEdge: number; // pts
  minScore: number;
  book: string;
  side: string;
  leansOnly: boolean;
  from: string; // date
}

export const BT_DEFAULT: BtFilter = { market: "", line: "", minProb: 0, minEdge: 0, minScore: 0, book: "", side: "", leansOnly: true, from: "" };

const dec = (a: number) => 1 + (a > 0 ? a / 100 : 100 / -a);
const american = (d: number) => (d >= 2 ? Math.round((d - 1) * 100) : Math.round(-100 / (d - 1)));
const propKey = (r: Pick<BtRow, "game" | "market" | "player_id" | "line" | "side">) =>
  `${r.game}|${r.market}|${r.player_id ?? 0}|${r.line ?? ""}|${r.side}`;

/** Rows that pass the filters, then one bet per prop: the best price among books that pass. */
export function bets(rows: BtRow[], f: BtFilter): BtRow[] {
  const best = new Map<string, BtRow>();
  for (const r of rows) {
    if (r.price === null) continue;
    if (f.leansOnly && !r.lean) continue;
    if (f.market && r.market !== f.market) continue;
    if (f.line !== "" && r.line !== Number(f.line)) continue;
    if (r.p * 100 < f.minProb) continue;
    if (f.minEdge > 0 && (r.edge === null || r.edge * 100 < f.minEdge)) continue;
    if (f.minScore > 0 && (r.pi === null || r.pi < f.minScore)) continue;
    if (f.book && r.book !== f.book) continue;
    if (f.side && r.side !== f.side) continue;
    if (f.from && r.date < f.from) continue;
    const k = propKey(r);
    const cur = best.get(k);
    if (!cur || dec(r.price) > dec(cur.price!)) best.set(k, r);
  }
  return [...best.values()].sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id);
}

export interface Summary {
  bets: number;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  win_pct: number | null;
  units: number;
  roi: number | null;
  avg_odds: number | null;
  clv_cents: number | null; // average, bets with a closing price
  clv_ev: number | null; // average value at the closing no-vig price, per unit
  beat_close: number | null; // share of bets that beat the closing price
  drawdown: number; // worst peak-to-trough in units
}

/** Bets with their own price and stake (default 1 unit at the row's price). */
export function summarize(items: { r: BtRow; price?: number; stake?: number }[]): Summary {
  let wins = 0, losses = 0, pushes = 0, voids = 0, units = 0, staked = 0, peak = 0, dd = 0;
  const decs: number[] = [];
  const clvC: number[] = [];
  const clvE: number[] = [];
  for (const { r, price = r.price!, stake = 1 } of items) {
    if (r.result === "void") {
      voids++;
      continue;
    }
    decs.push(dec(price));
    staked += stake;
    if (r.result === "push") pushes++;
    else if (r.result === "win") {
      wins++;
      units += stake * (dec(price) - 1);
    } else {
      losses++;
      units -= stake;
    }
    peak = Math.max(peak, units);
    dd = Math.max(dd, peak - units);
    if (r.close !== null) clvC.push(cents(price, r.close));
    if (r.clv !== null) clvE.push(r.clv);
  }
  const graded = wins + losses;
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  const avgDec = avg(decs);
  return {
    bets: items.length,
    wins,
    losses,
    pushes,
    voids,
    win_pct: graded ? wins / graded : null,
    units,
    roi: staked ? units / staked : null,
    avg_odds: avgDec ? american(avgDec) : null,
    clv_cents: avg(clvC),
    clv_ev: avg(clvE),
    beat_close: clvC.length ? clvC.filter((c) => c > 0).length / clvC.length : null,
    drawdown: dd,
  };
}

export type GroupBy = "player" | "market" | "book" | "version";

export function clvBy(rows: BtRow[], by: GroupBy, min = 5): { key: string; n: number; clv_cents: number; clv_ev: number | null }[] {
  const groups = new Map<string, BtRow[]>();
  for (const r of rows) {
    if (r.close === null || r.price === null) continue;
    const k = (by === "market" ? r.label : by === "player" ? r.player : by === "book" ? r.book : r.version) ?? "—";
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  return [...groups]
    .filter(([, rs]) => rs.length >= min)
    .map(([key, rs]) => {
      const s = summarize(rs.map((r) => ({ r })));
      return { key, n: rs.length, clv_cents: s.clv_cents ?? 0, clv_ev: s.clv_ev };
    })
    .sort((a, b) => b.n - a.n);
}

export { propKey };
