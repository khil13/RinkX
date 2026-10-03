import { describe, expect, it } from "vitest";
import type { PropRow } from "./data/types";
import { ADVANCED, matches, SORTS } from "./propFilters";
import { buildReport, reportText } from "./report";

const row = (o: Partial<PropRow> & { score?: number; hit?: [number, number]; pp?: number; slot?: string }): PropRow =>
  ({
    prediction_id: Math.random(),
    subject: { type: "player", id: 1, name: o.subject?.name ?? "Ann Example", team: "BOS", position: "C" },
    game: { id: 10, date: "2026-10-03", start_time_utc: "2026-10-03T23:00:00Z", home: "BOS", away: "TOR" },
    market: "skater_shots_on_goal",
    market_label: "Shots on Goal",
    kind: "over_under",
    book: "fd",
    book_name: "FanDuel",
    source: "The Odds API",
    line: 2.5,
    over_price: -110,
    under_price: -110,
    lean: "over",
    side_scored: "over",
    price: -110,
    p_model: 0.6,
    p_market: 0.5,
    market_is_novig: true,
    edge: 0.1,
    ev: 0.15,
    confidence: 70,
    confidence_parts: null,
    calculation: [],
    data_quality: 0.9,
    missing_inputs: [],
    line_seen_at: "",
    line_changed_at: "",
    priced_at: "",
    projection: 3.2,
    scores: {
      intelligence: o.score ?? 70,
      intelligence_parts: [],
      shot_environment: 60,
      shot_env_parts: [],
      value: 60,
      config_version: "1",
      why: [],
      summary: "",
      facts: { side: "over", line: 2.5, price: -110, projection_diff: 0.7, l10_hit: o.hit, pp_unit: o.pp, line_slot: o.slot },
    },
    ...o,
  }) as PropRow;

describe("advanced filters", () => {
  it("filter on published values only; a missing value never passes a filter that needs it", () => {
    const a = row({ hit: [7, 10], pp: 1, slot: "F1" });
    const b = row({ subject: { type: "player", id: 2, name: "Bo Sample", team: "TOR", position: "D" } as PropRow["subject"] });
    expect(matches(a, { ...ADVANCED, minHit: 70, pp1: true, topLine: true, venue: "home" })).toBe(true);
    expect(matches(b, { ...ADVANCED, minHit: 50 })).toBe(false); // no hit rate published
    expect(matches(b, { ...ADVANCED, player: "bo s", venue: "away" })).toBe(true);
    expect(matches(a, { ...ADVANCED, minOdds: "+100" })).toBe(false); // -110 pays less than +100
    expect(matches(a, { ...ADVANCED, maxOdds: "-105" })).toBe(true);
  });
  it("sorts by Prop Intelligence, projection edge and hit rate", () => {
    const xs = [row({ score: 50, hit: [3, 10] }), row({ score: 90, hit: [8, 10] })];
    expect([...xs].sort(SORTS.intelligence)[0]!.scores!.intelligence).toBe(90);
    expect([...xs].sort(SORTS.hit)[0]!.scores!.facts.l10_hit).toEqual([8, 10]);
  });
});

describe("daily report", () => {
  it("states only published numbers and says when a section is empty", () => {
    const s = buildReport({ props: [row({ score: 88 })], moves: [], news: [], games: [], stale: false });
    const best = s.find((x) => x.title === "Best props today")!;
    expect(best.lines[0]).toContain("Ann Example (BOS) Over 2.5 Shots on Goal −110 at FanDuel");
    expect(best.lines[0]).toContain("model 60.0% · market 50.0% · edge +10.0 pts · Prop Intelligence 88/100");
    expect(s.find((x) => x.title === "Best goal props")!.lines).toEqual([]);
    const text = reportText("2026-10-03", s);
    expect(text).toContain("• No goal lean today.");
    expect(text).not.toMatch(/guarantee|lock|free money|can't lose/i); // copy-lint: allow
  });
  it("flags stale data and suspicious leans", () => {
    const s = buildReport({ props: [row({ edge: 0.2, confidence: 40 })], moves: [], news: [], games: [], stale: true });
    const flags = s.find((x) => x.title === "Red flags")!.lines.join(" ");
    expect(flags).toContain("older than it should be");
    expect(flags).toContain("unusually large");
    expect(flags).toContain("confidence only 40/100");
  });
});
