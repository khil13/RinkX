import type { PropRow } from "./data/types";
import { GROUPS } from "./marketGroups";

// How the card is built (stated on the page too).
export const MAX_TEAM = 4;
export const PER_GROUP = 3; // player picks per market group (SOG, points, goals, ...)

const NAMES: Record<string, string> = {
  sog: "Shots on goal",
  points: "Points",
  goals: "Goals",
  assists: "Assists",
  blocks: "Blocked shots",
  hits: "Hits",
  goalie: "Goalies",
};

export interface CardGroup {
  key: string;
  title: string;
  shown: PropRow[];
  total: number; // leans in this group (one per player), before the cap
  priced: number; // priced lines in this group on this date (lean or not)
}

/** The card for one date: leans only, and only for games on that date's schedule (`gameIds`,
 * from the published slate); the best-priced book for each prop. Team props: one per game, by
 * expected value. Player props: by market group in a fixed order (SOG, points, goals, ...), up to
 * PER_GROUP each, by expected value, one pick per player across the card. Grouping keeps one
 * market (longshot goal props have the biggest EV) from filling every slot. */
export function buildCard(rows: PropRow[], date: string, gameIds: Set<number> | null) {
  const onSlate = rows.filter((r) => r.game.date === date && (gameIds === null || gameIds.has(r.game.id)));
  const leans = onSlate.filter((r) => r.lean);
  const bestBook = new Map<string, PropRow>();
  for (const r of leans) {
    const key = `${r.game.id}|${r.subject.name}|${r.market}|${r.line}|${r.lean}`;
    const cur = bestBook.get(key);
    if (!cur || (r.ev ?? -9) > (cur.ev ?? -9)) bestBook.set(key, r);
  }
  const byEv = [...bestBook.values()].sort((a, b) => (b.ev ?? -9) - (a.ev ?? -9));
  const onePer = (items: PropRow[], key: (r: PropRow) => string, max: number) => {
    const seen = new Set<string>();
    const out: PropRow[] = [];
    for (const r of items) {
      if (seen.has(key(r))) continue;
      seen.add(key(r));
      out.push(r);
    }
    return { shown: out.slice(0, max), total: out.length };
  };
  const used = new Set<string>();
  const groups: CardGroup[] = GROUPS.map((g) => {
    const seen = new Set<string>();
    const cands: PropRow[] = [];
    for (const r of byEv) {
      if (r.subject.type !== "player" || !g.markets.includes(r.market)) continue;
      const who = String(r.subject.id);
      if (used.has(who) || seen.has(who)) continue;
      seen.add(who);
      cands.push(r);
    }
    const shown = cands.slice(0, PER_GROUP);
    for (const r of shown) used.add(r.subject.type === "player" ? String(r.subject.id) : "");
    const priced = new Set(onSlate.filter((r) => r.subject.type === "player" && g.markets.includes(r.market)).map((r) => r.prediction_id)).size;
    return { key: g.key, title: NAMES[g.key] ?? g.key, shown, total: cands.length, priced };
  });
  const playerShown = groups.flatMap((g) => g.shown);
  return {
    team: onePer(byEv.filter((r) => r.subject.type === "game"), (r) => String(r.game.id), MAX_TEAM),
    player: { shown: playerShown, total: groups.reduce((n, g) => n + g.total, 0), groups },
    leans: leans.length,
  };
}
