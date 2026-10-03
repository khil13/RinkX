import { type SimInputs, type SimLeg, simulate, supported } from "./sim";
// Parlay combination: the same arithmetic as pipeline/rinkx/correlation/parlay.py, tested
// against vectors that Python generates (fixtures/parlay_vectors.json). Keep the two in step.

export const N_POINTS = 4096;
const PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37];
export const MAX_LEGS = PRIMES.length + 1;

const ERFC = [
  -1.26551223, 1.00002368, 0.37409196, 0.09678418, -0.18628806, 0.27886807, -1.13520398, 1.48851587, -0.82215223,
  0.17087277,
];

export function erfc(x: number): number {
  const z = Math.abs(x);
  const t = 1 / (1 + 0.5 * z);
  let poly = ERFC[ERFC.length - 1]!;
  for (let i = ERFC.length - 2; i >= 0; i--) poly = ERFC[i]! + t * poly;
  const r = t * Math.exp(-z * z + poly);
  return x >= 0 ? r : 2 - r;
}

export function cdf(x: number): number {
  return 0.5 * erfc(-x / Math.sqrt(2));
}

const A = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.38357751867269e2, -3.066479806614716e1, 2.506628277459239];
const B = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1, -1.328068155288572e1];
const C = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
const D = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416];

export function ppf(p: number): number {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  const lo = 0.02425;
  const [a0, a1, a2, a3, a4, a5] = A as [number, number, number, number, number, number];
  const [b0, b1, b2, b3, b4] = B as [number, number, number, number, number];
  const [c0, c1, c2, c3, c4, c5] = C as [number, number, number, number, number, number];
  const [d0, d1, d2, d3] = D as [number, number, number, number];
  if (p < lo) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c0 * q + c1) * q + c2) * q + c3) * q + c4) * q + c5) / ((((d0 * q + d1) * q + d2) * q + d3) * q + 1);
  }
  if (p > 1 - lo) {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c0 * q + c1) * q + c2) * q + c3) * q + c4) * q + c5) / ((((d0 * q + d1) * q + d2) * q + d3) * q + 1);
  }
  const q = p - 0.5;
  const r = q * q;
  return ((((((a0 * r + a1) * r + a2) * r + a3) * r + a4) * r + a5) * q) / (((((b0 * r + b1) * r + b2) * r + b3) * r + b4) * r + 1);
}

function cholesky(m: number[][]): number[][] | null {
  const n = m.length;
  const c = m.map(() => new Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = 0; j <= i; j++) {
      let s = m[i]![j]!;
      for (let k = 0; k < j; k++) s -= c[i]![k]! * c[j]![k]!;
      if (i === j) {
        if (s <= 1e-10) return null;
        c[i]![i] = Math.sqrt(s);
      } else {
        c[i]![j] = s / c[j]![j]!;
      }
    }
  }
  return c;
}

export function makePd(corr: number[][]): { c: number[][]; shrink: number } {
  let f = 1;
  for (let it = 0; it < 200; it++) {
    const m = corr.map((row, i) => row.map((v, j) => (i === j ? 1 : v * f)));
    const c = cholesky(m);
    if (c) return { c, shrink: f };
    f *= 0.9;
  }
  throw new Error("could not make the correlation matrix positive definite");
}

const frac = (x: number) => x - Math.floor(x);

/** P(every leg wins) under a Gaussian copula, and the shrink factor applied (1 = none). */
export function combine(probs: number[], corr: number[][]): { p: number; shrink: number } {
  const k = probs.length;
  if (k === 0) return { p: 1, shrink: 1 };
  if (k > MAX_LEGS) throw new Error(`at most ${MAX_LEGS} legs`);
  if (probs.some((p) => p <= 0)) return { p: 0, shrink: 1 };
  if (corr.every((row, i) => row.every((v, j) => i === j || v === 0))) {
    let out = 1;
    for (const p of probs) out *= p;
    return { p: out, shrink: 1 };
  }
  const { c, shrink } = makePd(corr);
  const b = probs.map((p) => ppf(Math.min(p, 1 - 1e-12)));
  const alphas = PRIMES.slice(0, k - 1).map(Math.sqrt);
  let total = 0;
  for (let j = 1; j <= N_POINTS; j++) {
    let e = cdf(b[0]! / c[0]![0]!);
    let f = e;
    const y: number[] = [];
    for (let i = 1; i < k; i++) {
      const w = frac(j * alphas[i - 1]!);
      y.push(ppf(Math.min(Math.max(w * e, 1e-15), 1 - 1e-15)));
      let s = 0;
      for (let m = 0; m < i; m++) s += c[i]![m]! * y[m]!;
      e = cdf((b[i]! - s) / c[i]![i]!);
      f *= e;
    }
    total += f;
  }
  return { p: total / N_POINTS, shrink };
}

// ---- legs and their correlations ------------------------------------------------------------

export interface Leg {
  key: string; // game|subject|market|side
  prediction_id: number;
  subject: string;
  player_id: number | null;
  team: string | null;
  position: string | null;
  game_id: number;
  home: string;
  away: string;
  start_time_utc: string;
  market: string;
  market_label: string;
  line: number | null;
  side: "over" | "under" | "yes" | "no" | "home" | "away";
  book_name: string;
  price: number;
  p_model: number;
}

export interface CorrPair {
  a: string;
  b: string;
  relation: "same_player" | "same_team" | "opponent" | "opp_goalie";
  rho: number;
  ci: [number, number];
  n: number;
  method: string;
}

export interface Correlations {
  generated_at: string;
  min_n: number;
  aliases: Record<string, string>;
  pairs: CorrPair[];
  window: { from: string; to: string } | null;
  computed_at: string | null;
}

export type PairSource = "estimate" | "different_games" | "not_estimated" | "too_little_data";

export interface PairUsed {
  i: number;
  j: number;
  rho: number; // oriented: positive = the two legs tend to win together
  source: PairSource;
  relation?: string;
  n?: number;
  ci?: [number, number];
}

const GOALIE_MARKETS = new Set(["goalie_saves", "goalie_goals_against"]);
const sign = (side: Leg["side"]) => (side === "under" || side === "no" ? -1 : 1);

export function conflict(a: Leg, b: Leg): boolean {
  return a.game_id === b.game_id && a.subject === b.subject && a.market === b.market;
}

export function pairFor(a: Leg, b: Leg, corr: Correlations | null): Omit<PairUsed, "i" | "j"> {
  if (a.game_id !== b.game_id) return { rho: 0, source: "different_games" };
  if (!corr || a.player_id === null || b.player_id === null) return { rho: 0, source: "not_estimated" };
  const ma = corr.aliases[a.market] ?? a.market;
  const mb = corr.aliases[b.market] ?? b.market;
  const ga = GOALIE_MARKETS.has(ma);
  const gb = GOALIE_MARKETS.has(mb);
  let relation: CorrPair["relation"];
  if (ga && gb) return { rho: 0, source: "not_estimated" };
  if (ga || gb) {
    if (a.team === b.team) return { rho: 0, source: "not_estimated" }; // a goalie and his own skaters
    relation = "opp_goalie";
  } else if (a.player_id === b.player_id) relation = "same_player";
  else relation = a.team === b.team ? "same_team" : "opponent";
  const [x, y] = gb ? [ma, mb] : ga ? [mb, ma] : [ma, mb];
  const hit = corr.pairs.find(
    (p) => p.relation === relation && ((p.a === x && p.b === y) || (p.a === y && p.b === x)),
  );
  if (!hit) return { rho: 0, source: "not_estimated", relation };
  if (hit.n < corr.min_n) return { rho: 0, source: "too_little_data", relation, n: hit.n, ci: hit.ci };
  return { rho: hit.rho * sign(a.side) * sign(b.side), source: "estimate", relation, n: hit.n, ci: hit.ci };
}

export function decimal(american: number): number {
  return 1 + (american > 0 ? american / 100 : 100 / -american);
}

export function toAmerican(dec: number): number {
  return dec >= 2 ? Math.round((dec - 1) * 100) : Math.round(-100 / (dec - 1));
}

export interface GroupUsed {
  game_id: number;
  legs: number[]; // indexes into the legs
  method: "single" | "simulation" | "correlations";
  p: number;
  /** simulation: P_sim(all) / product of P_sim(each); 1 = the legs move independently */
  lift?: number;
  n?: number;
  together?: number; // simulated games in which every leg won
  why_not_sim?: "no_sim" | "unsupported_leg" | "too_rare";
}

export interface ParlayResult {
  pairs: PairUsed[];
  groups: GroupUsed[];
  p_independent: number;
  p_adjusted: number;
  shrink: number;
  fair_decimal: number | null;
  offered_decimal: number;
  ev: number;
}

export const MIN_TOGETHER = 30; // simulated joint wins needed to trust the simulated lift

/** Same-game legs: the simulation's lift on the legs' own probabilities when it can settle every
 * leg; otherwise the correlation copula. Legs in different games are independent. */
export function evaluate(legs: Leg[], corr: Correlations | null, sims: Record<number, SimInputs | null> = {}): ParlayResult {
  const k = legs.length;
  const m: number[][] = legs.map((_, i) => legs.map((__, j) => (i === j ? 1 : 0)));
  const pairs: PairUsed[] = [];
  for (let i = 0; i < k; i++) {
    for (let j = i + 1; j < k; j++) {
      const p = pairFor(legs[i]!, legs[j]!, corr);
      pairs.push({ i, j, ...p });
      m[i]![j] = p.rho;
      m[j]![i] = p.rho;
    }
  }
  const probs = legs.map((l) => l.p_model);
  const indep = probs.reduce((a, b) => a * b, 1);
  const byGame = new Map<number, number[]>();
  legs.forEach((l, i) => byGame.set(l.game_id, [...(byGame.get(l.game_id) ?? []), i]));
  const groups: GroupUsed[] = [];
  let shrink = 1;
  for (const [game_id, idx] of byGame) {
    const ps = idx.map((i) => probs[i]!);
    const prod = ps.reduce((a, b) => a * b, 1);
    if (idx.length === 1) {
      groups.push({ game_id, legs: idx, method: "single", p: prod });
      continue;
    }
    const x = sims[game_id] ?? null;
    let why: GroupUsed["why_not_sim"];
    if (!x) why = "no_sim";
    else {
      const simLegs: SimLeg[] = idx.map((i) => ({
        market: legs[i]!.market,
        player: legs[i]!.player_id,
        line: legs[i]!.line,
        side: legs[i]!.side,
      }));
      if (!simLegs.every((l) => supported(x, l))) why = "unsupported_leg";
      else {
        const r = simulate(x, simLegs);
        if (r.all < MIN_TOGETHER || r.each.some((e) => e === 0)) why = "too_rare";
        else {
          const lift = r.all / r.n / r.each.reduce((a, e) => a * (e / r.n), 1);
          const lo = Math.max(0, ps.reduce((a, b) => a + b, 0) - (ps.length - 1));
          const p = Math.min(Math.max(prod * lift, lo), Math.min(...ps));
          groups.push({ game_id, legs: idx, method: "simulation", p, lift, n: r.n, together: r.all });
          continue;
        }
      }
    }
    const sub = idx.map((i) => idx.map((j) => m[i]![j]!));
    const c = combine(ps, sub);
    shrink = Math.min(shrink, c.shrink);
    groups.push({ game_id, legs: idx, method: "correlations", p: c.p, why_not_sim: why });
  }
  const p = groups.reduce((a, g) => a * g.p, 1);
  const offered = legs.reduce((a, l) => a * decimal(l.price), 1);
  return {
    pairs,
    groups,
    p_independent: indep,
    p_adjusted: p,
    shrink,
    fair_decimal: p > 0 ? 1 / p : null,
    offered_decimal: offered,
    ev: p * offered - 1,
  };
}
