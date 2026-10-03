// Same-game simulation: the same steps as pipeline/rinkx/correlation/sim.py, tested against
// vectors that Python generates (fixtures/sim_vectors.json); the counts must match exactly.
// Keep the two in step. The pipeline publishes the inputs (sim/{game}.json); the browser runs it.

export interface SimInputs {
  version: number;
  game_id: number;
  home: string;
  away: string;
  goals: { home: number[]; away: number[] };
  s_home: number;
  ot_q: number;
  so_home: number;
  playoff: boolean;
  pace: number[];
  assists_per_goal: number[];
  boost: number;
  skaters: { id: number; side: "home" | "away"; g: number; a: number; shots: number[][] }[];
  mates: Record<string, number[]>;
  goalies: { home: number | null; away: number | null };
  sources: Record<string, unknown>;
}

export interface SimLeg {
  market: string;
  player?: number | null;
  line?: number | null;
  side: string;
}

export const N_SIMS = 20000;

export const SIM_MARKETS: Record<string, string> = {
  skater_shots_on_goal: "shots",
  skater_goals: "goals",
  skater_anytime_goal: "goals",
  skater_assists: "assists",
  skater_points: "points",
  skater_first_goal: "first_goal",
  goalie_saves: "saves",
  goalie_goals_against: "goals_against",
  goalie_win: "win",
  goalie_shutout: "shutout",
  goalie_saves_and_win: "saves_win",
  game_moneyline: "moneyline",
  game_total: "game_total",
};

/** mulberry32: the same 32-bit integer steps as the Python Rng. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0;
    t = (((t + (Math.imul(t ^ (t >>> 7), t | 61) >>> 0)) >>> 0) ^ t) >>> 0;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function cumulative(w: number[]): number[] {
  const out: number[] = [];
  let s = 0;
  for (const x of w) {
    s += x;
    out.push(s);
  }
  return out;
}

function pick(cum: number[], u: number): number {
  const x = u * cum[cum.length - 1]!;
  let lo = 0;
  let hi = cum.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (cum[mid]! > x) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

type Side = "home" | "away";
const SIDES: Side[] = ["home", "away"];

interface Game {
  goals: Map<number, number>;
  assists: Map<number, number>;
  shots: Map<number, number>;
  teamGoals: Record<Side, number>;
  teamShots: Record<Side, number>;
  winner: Side;
  shootout: boolean;
  firstScorer: number | null;
}

class Sim {
  private cumGoals: Record<Side, number[]>;
  private cumApg: number[];
  private sideIdx: Record<Side, number[]>;
  private cumScorer: Record<Side, number[]>;
  private cumShots: number[][][];
  private mates: Map<number, Set<number>>;

  constructor(private x: SimInputs) {
    this.cumGoals = { home: cumulative(x.goals.home), away: cumulative(x.goals.away) };
    this.cumApg = cumulative(x.assists_per_goal);
    this.sideIdx = { home: [], away: [] };
    x.skaters.forEach((k, i) => this.sideIdx[k.side].push(i));
    this.cumScorer = {
      home: cumulative(this.sideIdx.home.map((i) => x.skaters[i]!.g)),
      away: cumulative(this.sideIdx.away.map((i) => x.skaters[i]!.g)),
    };
    this.cumShots = x.skaters.map((k) => k.shots.map(cumulative));
    this.mates = new Map(Object.entries(x.mates).map(([k, v]) => [Number(k), new Set(v)]));
  }

  private assister(r: () => number, side: Side, scorer: number, taken: number[]): number | null {
    const sk = this.x.skaters;
    const idx = this.sideIdx[side].filter((i) => sk[i]!.id !== scorer && !taken.includes(sk[i]!.id));
    if (idx.length === 0) return null;
    const mates = this.mates.get(scorer);
    const b = this.x.boost;
    const w = idx.map((i) => sk[i]!.a * (mates?.has(sk[i]!.id) ? b : 1.0));
    return sk[idx[pick(cumulative(w), r())]!]!.id;
  }

  private goal(r: () => number, side: Side, g: Game): number {
    const idx = this.sideIdx[side];
    const scorer = this.x.skaters[idx[pick(this.cumScorer[side], r())]!]!.id;
    g.goals.set(scorer, (g.goals.get(scorer) ?? 0) + 1);
    const nAst = pick(this.cumApg, r());
    const taken: number[] = [];
    for (let k = 0; k < nAst; k++) {
      const a = this.assister(r, side, scorer, taken);
      if (a === null) break;
      g.assists.set(a, (g.assists.get(a) ?? 0) + 1);
      taken.push(a);
    }
    return scorer;
  }

  one(r: () => number): Game {
    const x = this.x;
    const nPace = x.pace.length;
    const pace = { home: Math.floor(r() * nPace), away: Math.floor(r() * nPace) };
    const reg = { home: pick(this.cumGoals.home, r()), away: pick(this.cumGoals.away, r()) };
    let ot: Side | null = null;
    let shootout = false;
    if (reg.home === reg.away) {
      if (x.playoff || r() < x.ot_q) ot = r() < x.s_home ? "home" : "away";
      else shootout = true;
    }
    const g: Game = {
      goals: new Map(),
      assists: new Map(),
      shots: new Map(),
      teamGoals: { home: 0, away: 0 },
      teamShots: { home: 0, away: 0 },
      winner: "home",
      shootout,
      firstScorer: null,
    };
    const scorers: number[] = [];
    for (const side of SIDES) for (let k = 0; k < reg[side]; k++) scorers.push(this.goal(r, side, g));
    const otScorer = ot !== null ? this.goal(r, ot, g) : null;
    g.firstScorer = scorers.length > 0 ? scorers[Math.floor(r() * scorers.length)]! : otScorer;
    for (const s of SIDES) g.teamGoals[s] = reg[s] + (ot === s ? 1 : 0);
    x.skaters.forEach((k, i) => {
      g.shots.set(k.id, (g.goals.get(k.id) ?? 0) + pick(this.cumShots[i]![pace[k.side]]!, r()));
    });
    for (const s of SIDES) g.teamShots[s] = this.sideIdx[s].reduce((t, i) => t + g.shots.get(x.skaters[i]!.id)!, 0);
    if (shootout) g.winner = r() < x.so_home ? "home" : "away";
    else g.winner = g.teamGoals.home > g.teamGoals.away ? "home" : "away";
    return g;
  }
}

function goalieSide(x: SimInputs, player: number | null | undefined): Side | null {
  for (const s of SIDES) if (player != null && x.goalies[s] === player) return s;
  return null;
}

const GOALIE_KINDS = new Set(["saves", "goals_against", "win", "shutout", "saves_win"]);

export function supported(x: SimInputs, leg: SimLeg): boolean {
  const kind = SIM_MARKETS[leg.market];
  if (!kind) return false;
  if (kind === "moneyline" || kind === "game_total") return true;
  if (GOALIE_KINDS.has(kind)) return goalieSide(x, leg.player) !== null;
  return x.skaters.some((k) => k.id === leg.player);
}

function outcome(x: SimInputs, leg: SimLeg, g: Game): boolean {
  const kind = SIM_MARKETS[leg.market]!;
  const { side, line } = leg;
  const pid = leg.player ?? null;
  let v: number;
  if (kind === "moneyline") return g.winner === side;
  if (kind === "game_total") v = g.teamGoals.home + g.teamGoals.away + (g.shootout ? 1 : 0);
  else if (GOALIE_KINDS.has(kind)) {
    const me = goalieSide(x, pid)!;
    const opp: Side = me === "home" ? "away" : "home";
    const ga = g.teamGoals[opp];
    if (kind === "win") return (g.winner === me) === (side === "yes");
    if (kind === "shutout") return (ga === 0) === (side === "yes");
    const saves = g.teamShots[opp] - ga;
    if (kind === "saves_win") return (g.winner === me && line != null && saves > line) === (side === "yes");
    v = kind === "saves" ? saves : ga;
  } else if (kind === "first_goal") return (g.firstScorer === pid) === (side === "yes");
  else {
    const gl = g.goals.get(pid!) ?? 0;
    const a = g.assists.get(pid!) ?? 0;
    v = kind === "shots" ? (g.shots.get(pid!) ?? 0) : kind === "goals" ? gl : kind === "assists" ? a : gl + a;
  }
  if (side === "yes" || side === "no") return v >= 1 === (side === "yes");
  return side === "over" ? v > line! : v < line!;
}

export interface SimResult {
  n: number;
  each: number[];
  all: number;
}

/** Win counts for each leg and for all of them together, over n simulated games. */
export function simulate(x: SimInputs, legs: SimLeg[], n: number = N_SIMS, seed = 1): SimResult {
  const sim = new Sim(x);
  const r = rng(seed);
  const each = legs.map(() => 0);
  let all = 0;
  for (let k = 0; k < n; k++) {
    const g = sim.one(r);
    let every = true;
    legs.forEach((leg, i) => {
      if (outcome(x, leg, g)) each[i]!++;
      else every = false;
    });
    if (every) all++;
  }
  return { n, each, all };
}
