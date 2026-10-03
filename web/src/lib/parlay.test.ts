import { describe, expect, it } from "vitest";
import vectors from "../../../fixtures/parlay_vectors.json";
import simVectors from "../../../fixtures/sim_vectors.json";
import type { SimInputs } from "./sim";
import { combine, type Correlations, evaluate, type Leg, N_POINTS, pairFor, toAmerican } from "./parlay";

// Vectors come from the Python reference (pipeline/rinkx/correlation/parlay.py), so passing here
// proves the browser combines legs exactly as the pipeline does.
describe("parlay copula matches the Python reference", () => {
  it("uses the same lattice size", () => expect(N_POINTS).toBe(vectors.n_points));
  for (const c of vectors.cases) {
    it(`${c.probs.length} legs ${JSON.stringify(c.corr)}`, () => {
      const out = combine(c.probs, c.corr);
      expect(out.p).toBeCloseTo(c.p, 9);
      expect(out.shrink).toBeCloseTo(c.shrink, 12);
    });
  }
});

const leg = (o: Partial<Leg>): Leg => ({
  key: "k",
  prediction_id: 1,
  subject: "A",
  player_id: 1,
  team: "BOS",
  position: "C",
  game_id: 10,
  home: "BOS",
  away: "TOR",
  start_time_utc: "2026-01-01T00:00:00Z",
  market: "skater_shots_on_goal",
  market_label: "Shots on Goal",
  line: 2.5,
  side: "over",
  book_name: "FanDuel",
  price: -110,
  p_model: 0.55,
  ...o,
});

const corr: Correlations = {
  generated_at: "",
  min_n: 300,
  aliases: { skater_anytime_goal: "skater_goals" },
  window: null,
  computed_at: null,
  pairs: [
    { a: "skater_shots_on_goal", b: "skater_goals", relation: "same_player", rho: 0.3, ci: [0.28, 0.32], n: 9000, method: "m" },
    { a: "skater_shots_on_goal", b: "skater_points", relation: "same_team", rho: 0.05, ci: [0.03, 0.07], n: 200, method: "m" },
    { a: "skater_shots_on_goal", b: "goalie_saves", relation: "opp_goalie", rho: 0.2, ci: [0.18, 0.22], n: 2000, method: "m" },
  ],
};

describe("pairing legs", () => {
  it("same player, oriented by side, with the anytime-goal alias", () => {
    const a = leg({});
    expect(pairFor(a, leg({ market: "skater_anytime_goal", side: "yes" }), corr)).toMatchObject({ rho: 0.3, source: "estimate" });
    expect(pairFor(a, leg({ market: "skater_goals", side: "under" }), corr).rho).toBeCloseTo(-0.3);
  });
  it("different games are independent; thin estimates are not used", () => {
    expect(pairFor(leg({}), leg({ game_id: 11 }), corr).source).toBe("different_games");
    const p = pairFor(leg({}), leg({ player_id: 2, subject: "B", market: "skater_points" }), corr);
    expect(p).toMatchObject({ rho: 0, source: "too_little_data", n: 200 });
  });
  it("skater vs the opposing goalie, in either order; not with his own goalie", () => {
    const g = leg({ player_id: 9, subject: "G", team: "TOR", position: "G", market: "goalie_saves" });
    expect(pairFor(g, leg({}), corr)).toMatchObject({ rho: 0.2, relation: "opp_goalie" });
    expect(pairFor(leg({}), g, corr).rho).toBe(0.2);
    expect(pairFor(leg({}), { ...g, team: "BOS" }, corr).source).toBe("not_estimated");
  });
  it("game markets are not estimated", () => {
    expect(pairFor(leg({}), leg({ player_id: null, market: "game_total" }), corr).source).toBe("not_estimated");
  });
});

describe("evaluate", () => {
  it("positive correlation raises the joint probability above independence", () => {
    const r = evaluate([leg({}), leg({ market: "skater_goals", price: 250, p_model: 0.3 })], corr);
    expect(r.p_independent).toBeCloseTo(0.165, 10);
    expect(r.p_adjusted).toBeGreaterThan(r.p_independent);
    expect(r.offered_decimal).toBeCloseTo((1 + 100 / 110) * 3.5, 10);
    expect(r.ev).toBeCloseTo(r.p_adjusted * r.offered_decimal - 1, 12);
  });
  it("american odds round-trip", () => {
    expect(toAmerican(2.5)).toBe(150);
    expect(toAmerican(1.5)).toBe(-200);
  });
});

describe("evaluate with the same-game simulation", () => {
  const x = simVectors.inputs as unknown as SimInputs;
  const mate = (id: number, o: Partial<Leg> = {}) =>
    leg({ game_id: 1, player_id: id, subject: `P${id}`, market: "skater_points", line: 0.5, p_model: 0.5, ...o });
  it("uses the simulation's lift when it can settle every leg in the game", () => {
    const r = evaluate([mate(100), mate(101)], corr, { 1: x });
    const g = r.groups[0]!;
    expect(g.method).toBe("simulation");
    expect(g.lift!).toBeGreaterThan(1); // linemates' points move together
    expect(r.p_adjusted).toBeCloseTo(0.25 * g.lift!, 10);
    expect(r.p_adjusted).toBeLessThanOrEqual(0.5);
  });
  it("falls back to correlations for a leg it can't settle, or without simulation inputs", () => {
    expect(evaluate([mate(100), mate(101, { market: "skater_hits" })], corr, { 1: x }).groups[0]).toMatchObject({
      method: "correlations",
      why_not_sim: "unsupported_leg",
    });
    expect(evaluate([mate(100), mate(101)], corr, {}).groups[0]).toMatchObject({ why_not_sim: "no_sim" });
  });
  it("legs in different games multiply", () => {
    const r = evaluate([mate(100), mate(101, { game_id: 2 })], corr, { 1: x });
    expect(r.groups.map((g) => g.method)).toEqual(["single", "single"]);
    expect(r.p_adjusted).toBeCloseTo(0.25, 12);
  });
});
