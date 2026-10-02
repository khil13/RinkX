// Parlay legs live on this device only (localStorage), shared across pages via a tiny store.
import { useSyncExternalStore } from "react";
import type { PropRow } from "./data/types";
import type { Leg } from "./parlay";

const KEY = "rinkx.parlay.legs";
const listeners = new Set<() => void>();
let cache: Leg[] | null = null;

function read(): Leg[] {
  if (cache) return cache;
  try {
    cache = JSON.parse(localStorage.getItem(KEY) ?? "[]") as Leg[];
  } catch {
    cache = [];
  }
  return cache;
}

function write(legs: Leg[]) {
  cache = legs;
  try {
    localStorage.setItem(KEY, JSON.stringify(legs));
  } catch {
    /* storage blocked: the parlay lasts for this visit */
  }
  listeners.forEach((l) => l());
}

export function legFromRow(r: PropRow): Leg | null {
  const side = (r.lean ?? r.side_scored) as Leg["side"];
  if (r.price === null) return null;
  const subject = r.subject.name;
  return {
    key: `${r.game.id}|${subject}|${r.market}|${side}`,
    prediction_id: r.prediction_id,
    subject,
    player_id: r.subject.type === "player" ? r.subject.id : null,
    team: r.subject.team,
    position: r.subject.type === "player" ? r.subject.position : null,
    game_id: r.game.id,
    home: r.game.home,
    away: r.game.away,
    start_time_utc: r.game.start_time_utc,
    market: r.market,
    market_label: r.market_label,
    line: r.line,
    side,
    book_name: r.book_name,
    price: r.price,
    p_model: r.p_model,
  };
}

export const parlayStore = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  legs: read,
  add(leg: Leg) {
    write([...read().filter((l) => !(l.game_id === leg.game_id && l.subject === leg.subject && l.market === leg.market)), leg]);
  },
  remove(key: string) {
    write(read().filter((l) => l.key !== key));
  },
  clear() {
    write([]);
  },
};

export function useParlayLegs(): Leg[] {
  return useSyncExternalStore(parlayStore.subscribe, parlayStore.legs, () => []);
}
