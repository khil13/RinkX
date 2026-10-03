// Parlay builder: picks legs from the whole slate by model probability, edge, price, Prop
// Intelligence (which carries matchup, deployment and line movement), and how the legs move
// together. It is a transparent greedy search, not a black box: each step adds the leg that most
// improves the objective for the chosen risk level, under the constraints below.
import type { PropRow } from "./data/types";
import { type Correlations, evaluate, type Leg, type ParlayResult } from "./parlay";
import { legFromRow } from "./parlayStore";
import { bestPerProp } from "../components/PropGroups";

export type Risk = "conservative" | "balanced" | "aggressive";

export interface RiskRule {
  label: string;
  minProb: number; // leg's model probability
  minEdge: number; // leg's edge (probability points)
  minScore: number; // Prop Intelligence (legs without a score are skipped)
  minConfidence: number;
  maxPerGame: number;
  maxMovementAgainst: number; // skip a leg whose price moved this far against it (market-movement part < this score)
  objective: string;
}

export const RISK: Record<Risk, RiskRule> = {
  conservative: {
    label: "Conservative",
    minProb: 0.58,
    minEdge: 0.03,
    minScore: 65,
    minConfidence: 60,
    maxPerGame: 2,
    maxMovementAgainst: 35,
    objective: "highest combined probability, with positive expected value",
  },
  balanced: {
    label: "Balanced",
    minProb: 0.5,
    minEdge: 0.03,
    minScore: 55,
    minConfidence: 50,
    maxPerGame: 2,
    maxMovementAgainst: 25,
    objective: "expected value weighted by the chance it wins",
  },
  aggressive: {
    label: "Aggressive",
    minProb: 0.3,
    minEdge: 0.04,
    minScore: 45,
    minConfidence: 40,
    maxPerGame: 3,
    maxMovementAgainst: 0,
    objective: "highest expected value",
  },
};

export interface BuildOptions {
  legs: number;
  risk: Risk;
  markets: string[] | null; // null = every market
  date: string | null; // games on this date only
}

export interface Built {
  legs: { row: PropRow; leg: Leg }[];
  result: ParlayResult | null;
  considered: number;
  eligible: number;
  short: boolean; // fewer eligible legs than asked for
}

function objective(r: ParlayResult, risk: Risk): number {
  if (risk === "conservative") return r.ev > 0 ? Math.log(r.p_adjusted) : -1e9 + r.ev;
  if (risk === "balanced") return r.ev * Math.sqrt(r.p_adjusted);
  return r.ev;
}

export function eligible(rows: PropRow[], o: BuildOptions): PropRow[] {
  const rule = RISK[o.risk];
  return bestPerProp(rows).filter((r) => {
    if (!r.lean || r.price === null) return false;
    if (o.date && r.game.date !== o.date) return false;
    if (o.markets && !o.markets.includes(r.market)) return false;
    if (Date.parse(r.game.start_time_utc) <= Date.now()) return false;
    const s = r.scores;
    if (!s || s.intelligence === null || s.intelligence < rule.minScore) return false;
    if (r.p_model < rule.minProb || (r.edge ?? -1) < rule.minEdge || (r.confidence ?? 0) < rule.minConfidence) return false;
    const mv = s.intelligence_parts.find((p) => p.part === "market_movement")?.score;
    return mv == null || mv >= rule.maxMovementAgainst;
  });
}

/** Greedy: start from the best single leg, then add whichever eligible leg most improves the
 * objective, never two legs on the same player/market, at most `maxPerGame` legs per game. */
export function build(rows: PropRow[], corr: Correlations | null, o: BuildOptions): Built {
  const rule = RISK[o.risk];
  const pool = eligible(rows, o)
    .map((row) => ({ row, leg: legFromRow(row) }))
    .filter((x): x is { row: PropRow; leg: Leg } => x.leg !== null);
  const chosen: { row: PropRow; leg: Leg }[] = [];
  let best: ParlayResult | null = null;
  while (chosen.length < o.legs) {
    let pick: { i: number; r: ParlayResult; v: number } | null = null;
    pool.forEach((c, i) => {
      if (chosen.some((x) => x.row.subject.name === c.row.subject.name && x.row.game.id === c.row.game.id)) return;
      if (chosen.filter((x) => x.row.game.id === c.row.game.id).length >= rule.maxPerGame) return;
      // the search uses correlations only (fast); the final result adds the same-game simulation
      const r = evaluate([...chosen.map((x) => x.leg), c.leg], corr);
      const v = objective(r, o.risk);
      if (!pick || v > pick.v) pick = { i, r, v };
    });
    if (!pick) break;
    const p: { i: number; r: ParlayResult; v: number } = pick;
    chosen.push(pool[p.i]!);
    best = p.r;
  }
  return { legs: chosen, result: best, considered: rows.length, eligible: pool.length, short: chosen.length < o.legs };
}

/** Correlation in words, only when the estimate supports it. */
export function corrLabel(rho: number, ci?: [number, number]): "Positive" | "Negative" | "No clear relationship" {
  if (ci) {
    if (ci[0] > 0) return "Positive";
    if (ci[1] < 0) return "Negative";
    return "No clear relationship";
  }
  return rho > 0.02 ? "Positive" : rho < -0.02 ? "Negative" : "No clear relationship";
}
