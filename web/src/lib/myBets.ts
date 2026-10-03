// "My performance": bets the user records on this device (localStorage only). Results and closing
// prices come from the published grading; nothing is entered by hand except the price and stake.
import { useSyncExternalStore } from "react";
import type { BtRow } from "./backtest";
import type { PropRow } from "./data/types";

export interface MyBet {
  id: string;
  prediction_id: number;
  game: number;
  market: string;
  label: string;
  player: string;
  player_id: number | null;
  line: number | null;
  side: string;
  book: string;
  price: number;
  stake: number;
  date: string; // game date
  placed_at: string;
}

const KEY = "rinkx.mybets";
const listeners = new Set<() => void>();
let cache: MyBet[] | null = null;

function read(): MyBet[] {
  if (cache) return cache;
  try {
    cache = JSON.parse(localStorage.getItem(KEY) ?? "[]") as MyBet[];
  } catch {
    cache = [];
  }
  return cache;
}

function write(bets: MyBet[]) {
  cache = bets;
  try {
    localStorage.setItem(KEY, JSON.stringify(bets));
  } catch {
    /* storage blocked: kept for this visit */
  }
  listeners.forEach((l) => l());
}

export function betFromRow(r: PropRow): MyBet | null {
  if (r.price === null) return null;
  return {
    id: `${r.prediction_id}-${Date.now()}`,
    prediction_id: r.prediction_id,
    game: r.game.id,
    market: r.market,
    label: r.market_label,
    player: r.subject.name,
    player_id: r.subject.type === "player" ? r.subject.id : null,
    line: r.line,
    side: r.lean ?? r.side_scored,
    book: r.book_name,
    price: r.price,
    stake: 1,
    date: r.game.date,
    placed_at: new Date().toISOString(),
  };
}

export const myBets = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  all: read,
  add(b: MyBet) {
    write([...read(), b]);
  },
  update(id: string, patch: Partial<MyBet>) {
    write(read().map((b) => (b.id === id ? { ...b, ...patch } : b)));
  },
  remove(id: string) {
    write(read().filter((b) => b.id !== id));
  },
};

export function useMyBets(): MyBet[] {
  return useSyncExternalStore(myBets.subscribe, myBets.all, () => []);
}

/** The graded row for a bet: the same prediction if published, else the same prop at any book. */
export function gradedFor(b: MyBet, rows: BtRow[]): BtRow | null {
  return (
    rows.find((r) => r.id === b.prediction_id) ??
    rows.find((r) => r.game === b.game && r.market === b.market && (r.player_id ?? null) === b.player_id && r.line === b.line && r.side === b.side) ??
    null
  );
}

export const MARKET_GROUPS: Record<string, string[]> = {
  SOG: ["skater_shots_on_goal"],
  Goals: ["skater_goals", "skater_anytime_goal", "skater_first_goal", "skater_pp_goal"],
  Points: ["skater_points", "skater_pp_points"],
  Assists: ["skater_assists", "skater_pp_assist"],
  Hits: ["skater_hits"],
  Blocks: ["skater_blocked_shots"],
};
