import type { PropRow } from "./data/types";

// How the card is built (stated on the page too).
export const MAX_TEAM = 4;
export const MAX_PLAYER = 8;

/** The card for one date: leans only, and only for games on that date's schedule (`gameIds`,
 * from the published slate); the best-priced book for each prop; then one pick per player (and
 * one per game for team props), ranked by expected value. */
export function buildCard(rows: PropRow[], date: string, gameIds: Set<number> | null) {
  const leans = rows.filter((r) => r.lean && r.game.date === date && (gameIds === null || gameIds.has(r.game.id)));
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
  return {
    team: onePer(byEv.filter((r) => r.subject.type === "game"), (r) => String(r.game.id), MAX_TEAM),
    player: onePer(byEv.filter((r) => r.subject.type === "player"), (r) => (r.subject.type === "player" ? String(r.subject.id) : r.subject.name), MAX_PLAYER),
    leans: leans.length,
  };
}
