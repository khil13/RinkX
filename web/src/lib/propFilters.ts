// Advanced prop filters and sorts (Props page, Game Prop Center, Today). Every filter reads a
// published value; a prop missing that value is excluded by a filter that needs it, never guessed.
import type { PropRow } from "./data/types";

export interface Advanced {
  player: string;
  team: string;
  line: string; // exact line, "" = any
  minOdds: string; // American, "" = any
  maxOdds: string;
  minProb: number; // model probability, %
  minScore: number; // Prop Intelligence
  minHit: number; // last-10 hit rate at the line, %
  pp1: boolean;
  topLine: boolean;
  venue: "" | "home" | "away";
}

export const ADVANCED: Advanced = {
  player: "",
  team: "",
  line: "",
  minOdds: "",
  maxOdds: "",
  minProb: 0,
  minScore: 0,
  minHit: 0,
  pp1: false,
  topLine: false,
  venue: "",
};

const dec = (american: number) => 1 + (american > 0 ? american / 100 : 100 / -american);

export function hitRate(r: PropRow): number | null {
  const h = r.scores?.facts.l10_hit;
  return h && h[1] ? h[0] / h[1] : null;
}

export function venue(r: PropRow): "home" | "away" | null {
  if (!r.subject.team) return null;
  return r.subject.team === r.game.home ? "home" : r.subject.team === r.game.away ? "away" : null;
}

export function matches(r: PropRow, a: Advanced): boolean {
  const facts = r.scores?.facts;
  if (a.player && !r.subject.name.toLowerCase().includes(a.player.trim().toLowerCase())) return false;
  if (a.team && r.subject.team !== a.team && !(r.subject.type === "game" && (r.game.home === a.team || r.game.away === a.team)))
    return false;
  if (a.line !== "" && r.line !== Number(a.line)) return false;
  if (a.minOdds !== "" && (r.price === null || dec(r.price) < dec(Number(a.minOdds)))) return false;
  if (a.maxOdds !== "" && (r.price === null || dec(r.price) > dec(Number(a.maxOdds)))) return false;
  if (r.p_model * 100 < a.minProb) return false;
  if (a.minScore > 0 && (r.scores?.intelligence ?? -1) < a.minScore) return false;
  if (a.minHit > 0) {
    const h = hitRate(r);
    if (h === null || h * 100 < a.minHit) return false;
  }
  if (a.pp1 && facts?.pp_unit !== 1) return false;
  if (a.topLine && !(facts?.line_slot === "F1" || facts?.line_slot === "D1")) return false;
  if (a.venue && venue(r) !== a.venue) return false;
  return true;
}

export type SortKey = "ev" | "edge" | "confidence" | "start" | "intelligence" | "projection" | "hit" | "odds";

const num = (x: number | null | undefined) => (x == null ? -Infinity : x);

export const SORTS: Record<SortKey, (a: PropRow, b: PropRow) => number> = {
  ev: (a, b) => num(b.ev) - num(a.ev),
  edge: (a, b) => num(b.edge) - num(a.edge),
  confidence: (a, b) => num(b.confidence) - num(a.confidence),
  start: (a, b) => a.game.start_time_utc.localeCompare(b.game.start_time_utc),
  intelligence: (a, b) => num(b.scores?.intelligence) - num(a.scores?.intelligence),
  // projection vs the line, on the side scored
  projection: (a, b) => num(projEdge(b)) - num(projEdge(a)),
  hit: (a, b) => num(hitRate(b)) - num(hitRate(a)),
  // best payout first
  odds: (a, b) => (b.price === null ? -1 : dec(b.price)) - (a.price === null ? -1 : dec(a.price)),
};

export const SORT_LABELS: Record<SortKey, string> = {
  ev: "Expected value",
  intelligence: "Prop Intelligence",
  edge: "Model edge",
  projection: "Projection vs line",
  hit: "Last-10 hit rate",
  odds: "Odds (best payout)",
  confidence: "Confidence",
  start: "Start time",
};

export function projEdge(r: PropRow): number | null {
  const d = r.scores?.facts.projection_diff;
  if (d == null) return null;
  const side = r.lean ?? r.side_scored;
  return side === "under" || side === "no" ? -d : d;
}
