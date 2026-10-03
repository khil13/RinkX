import { describe, expect, it } from "vitest";
import { build, corrLabel, eligible } from "./autoParlay";
import type { PropRow } from "./data/types";

let n = 0;
const row = (o: { name: string; game?: number; p?: number; edge?: number; score?: number; price?: number; market?: string }): PropRow =>
  ({
    prediction_id: ++n,
    subject: { type: "player", id: n, name: o.name, team: "BOS", position: "C" },
    game: { id: o.game ?? 1, date: "2099-01-01", start_time_utc: "2099-01-01T23:00:00Z", home: "BOS", away: "TOR" },
    market: o.market ?? "skater_shots_on_goal",
    market_label: "Shots on Goal",
    kind: "over_under",
    book: "fd",
    book_name: "FanDuel",
    source: "x",
    line: 2.5,
    over_price: o.price ?? -110,
    under_price: -110,
    lean: "over",
    side_scored: "over",
    price: o.price ?? -110,
    p_model: o.p ?? 0.6,
    p_market: (o.p ?? 0.6) - (o.edge ?? 0.06),
    market_is_novig: true,
    edge: o.edge ?? 0.06,
    ev: (o.p ?? 0.6) * (o.price && o.price > 0 ? 1 + o.price / 100 : 1 + 100 / 110) - 1,
    confidence: 70,
    confidence_parts: null,
    calculation: [],
    data_quality: 0.9,
    missing_inputs: [],
    line_seen_at: "",
    line_changed_at: "",
    priced_at: "",
    scores: { intelligence: o.score ?? 75, intelligence_parts: [], shot_environment: null, shot_env_parts: [], value: 60,
      config_version: "1", why: [], summary: "", facts: { side: "over", line: 2.5, price: o.price ?? -110 } },
  }) as PropRow;

describe("parlay builder", () => {
  const rows = [
    row({ name: "A", game: 1, p: 0.64 }),
    row({ name: "B", game: 1, p: 0.62 }),
    row({ name: "C", game: 1, p: 0.61 }),
    row({ name: "D", game: 2, p: 0.6 }),
    row({ name: "E", game: 3, p: 0.45, price: 200, edge: 0.08 }), // EV 0.45 × 3 − 1 = +0.35
    row({ name: "F", game: 4, p: 0.66, score: 40 }), // score too low for every level here
  ];
  it("each risk level has its own eligibility", () => {
    expect(eligible(rows, { legs: 2, risk: "conservative", markets: null, date: null }).map((r) => r.subject.name)).toEqual(["A", "B", "C", "D"]);
    expect(eligible(rows, { legs: 2, risk: "aggressive", markets: null, date: null }).map((r) => r.subject.name)).toContain("E");
  });
  it("never repeats a player, caps legs per game, and stops short rather than forcing weak legs", () => {
    const b = build(rows, null, { legs: 8, risk: "conservative", markets: null, date: null });
    const names = b.legs.map((x) => x.row.subject.name);
    expect(new Set(names).size).toBe(names.length);
    expect(b.legs.filter((x) => x.row.game.id === 1).length).toBeLessThanOrEqual(2);
    expect(b.short).toBe(true);
    expect(names).not.toContain("F");
    expect(b.result!.p_adjusted).toBeCloseTo(b.legs.reduce((a, x) => a * x.row.p_model, 1), 10); // no correlations given
  });
  it("aggressive goes after value, conservative after probability", () => {
    const agg = build(rows, null, { legs: 1, risk: "aggressive", markets: null, date: null });
    const con = build(rows, null, { legs: 1, risk: "conservative", markets: null, date: null });
    expect(agg.legs[0]!.row.subject.name).toBe("E");
    expect(con.legs[0]!.row.subject.name).toBe("A");
  });
  it("labels correlation only when the interval supports a sign", () => {
    expect(corrLabel(0.2, [0.1, 0.3])).toBe("Positive");
    expect(corrLabel(-0.2, [-0.3, -0.1])).toBe("Negative");
    expect(corrLabel(0.05, [-0.01, 0.11])).toBe("No clear relationship");
  });
});
