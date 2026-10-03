import { describe, expect, it } from "vitest";
import vectors from "../../../fixtures/sim_vectors.json";
import { rng, type SimInputs, type SimLeg, simulate, supported } from "./sim";

// Vectors come from the Python reference (pipeline/rinkx/correlation/sim.py). The simulation uses
// only integer random steps and pre-computed tables, so the counts must match exactly.
describe("same-game simulation matches the Python reference", () => {
  it("draws the same random numbers", () => {
    const r = rng(12345);
    for (const v of vectors.rng) expect(r()).toBe(v);
  });
  const inputs = vectors.inputs as unknown as SimInputs;
  for (const c of vectors.cases) {
    it(c.legs.map((l) => `${l.market} ${l.side}`).join(" + "), () => {
      const out = simulate(inputs, c.legs as SimLeg[], vectors.n, vectors.seed);
      expect(out.each).toEqual(c.each);
      expect(out.all).toBe(c.all);
    });
  }
  it("knows which legs it can settle", () => {
    expect(supported(inputs, { market: "skater_points", player: 100, line: 0.5, side: "over" })).toBe(true);
    expect(supported(inputs, { market: "skater_hits", player: 100, line: 1.5, side: "over" })).toBe(false);
    expect(supported(inputs, { market: "goalie_saves", player: 12345, line: 25.5, side: "over" })).toBe(false);
    expect(supported(inputs, { market: "skater_points", player: 999, line: 0.5, side: "over" })).toBe(false);
  });
});
