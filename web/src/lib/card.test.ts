import { describe, expect, it } from "vitest";
import { buildCard, PER_GROUP } from "./card";
import type { PropRow } from "./data/types";

let nextId = 1;
function row(o: {
  game?: number;
  date?: string;
  player?: number | null;
  market?: string;
  book?: string;
  lean?: PropRow["lean"];
  ev?: number;
}): PropRow {
  const player = o.player === undefined ? 1 : o.player;
  return {
    prediction_id: nextId++,
    subject:
      player === null
        ? { type: "game", name: "T01 @ T00", team: null }
        : { type: "player", id: player, name: `P${player}`, team: "T00", position: "C" },
    game: { id: o.game ?? 10, date: o.date ?? "2026-10-03", start_time_utc: "2026-10-03T23:00:00Z", home: "T00", away: "T01" },
    market: o.market ?? "skater_shots_on_goal",
    market_label: "Shots on Goal",
    kind: "over_under",
    book: o.book ?? "fanduel",
    book_name: o.book ?? "fanduel",
    source: "test",
    line: 2.5,
    over_price: -110,
    under_price: -110,
    lean: o.lean === undefined ? "over" : o.lean,
    side_scored: "over",
    price: -110,
    p_model: 0.6,
    p_market: 0.5,
    market_is_novig: true,
    edge: 10,
    ev: o.ev ?? 0.1,
    confidence: 70,
    confidence_parts: null,
    calculation: [],
    data_quality: 1,
    missing_inputs: [],
    line_seen_at: "2026-10-03T12:00:00Z",
    line_changed_at: "2026-10-03T12:00:00Z",
    priced_at: "2026-10-03T12:00:00Z",
  };
}

describe("buildCard", () => {
  it("keeps only leans on the chosen date's slate games", () => {
    const rows = [
      row({ player: 1 }),
      row({ player: 2, date: "2026-10-04", game: 11 }), // another day
      row({ player: 3, game: 99 }), // dated today but not on the published slate
      row({ player: 4, lean: null }), // no lean
    ];
    const card = buildCard(rows, "2026-10-03", new Set([10, 11]));
    expect(card.player.shown.map((r) => r.subject.name)).toEqual(["P1"]);
    expect(card.leans).toBe(1);
    // Without a slate yet, the date alone still rules out other days.
    expect(buildCard(rows, "2026-10-03", null).player.shown.map((r) => r.subject.name)).toEqual(["P1", "P3"]);
  });

  it("takes the best-priced book, then one pick per player and per game, ranked by EV", () => {
    const rows = [
      row({ player: 1, book: "fanduel", ev: 0.05 }),
      row({ player: 1, book: "betmgm", ev: 0.08 }),
      row({ player: 1, market: "skater_points", ev: 0.07 }), // same player: second prop dropped
      row({ player: 2, ev: 0.2 }),
      row({ player: null, market: "game_total", ev: 0.03 }),
      row({ player: null, market: "game_moneyline", ev: 0.04 }), // same game: one team pick
    ];
    const card = buildCard(rows, "2026-10-03", new Set([10]));
    expect(card.player.shown.map((r) => [r.subject.name, r.book])).toEqual([
      ["P2", "fanduel"],
      ["P1", "betmgm"],
    ]);
    expect(card.team.shown.map((r) => r.market)).toEqual(["game_moneyline"]);
    expect(card.team.total).toBe(1);
  });

  it("caps each market group but reports the total", () => {
    const rows = Array.from({ length: PER_GROUP + 3 }, (_, i) => row({ player: i + 1, ev: i / 100 }));
    const card = buildCard(rows, "2026-10-03", null);
    const sog = card.player.groups.find((g) => g.key === "sog")!;
    expect(sog.shown).toHaveLength(PER_GROUP);
    expect(sog.total).toBe(PER_GROUP + 3);
    expect(sog.priced).toBe(PER_GROUP + 3);
    expect(sog.shown[0]!.subject.name).toBe(`P${PER_GROUP + 3}`);
  });

  it("keeps longshot goal props from crowding out shots on goal", () => {
    const rows = [
      ...Array.from({ length: 10 }, (_, i) => row({ player: 100 + i, market: "skater_anytime_goal", ev: 0.5 + i / 100 })),
      row({ player: 1, ev: 0.04 }),
      row({ player: 2, ev: 0.06 }),
      row({ player: 3, market: "skater_points", ev: 0.05 }),
    ];
    const card = buildCard(rows, "2026-10-03", null);
    const by = Object.fromEntries(card.player.groups.map((g) => [g.key, g.shown.map((r) => r.subject.name)]));
    expect(by.sog).toEqual(["P2", "P1"]);
    expect(by.points).toEqual(["P3"]);
    expect(by.goals).toHaveLength(PER_GROUP);
    expect(card.player.shown.slice(0, 2).map((r) => r.market)).toEqual(["skater_shots_on_goal", "skater_shots_on_goal"]);
  });

  it("one pick per player across groups, SOG first", () => {
    const rows = [row({ player: 1, ev: 0.02 }), row({ player: 1, market: "skater_anytime_goal", ev: 0.9 })];
    const card = buildCard(rows, "2026-10-03", null);
    expect(card.player.shown.map((r) => r.market)).toEqual(["skater_shots_on_goal"]);
  });
});
