import { describe, expect, it } from "vitest";
import { BT_DEFAULT, type BtRow, bets, clvBy, summarize } from "./backtest";

let id = 0;
const r = (o: Partial<BtRow>): BtRow => ({
  id: ++id, date: "2026-01-01", game: 1, market: "skater_shots_on_goal", label: "Shots on Goal", player: "A",
  player_id: 1, book: "FanDuel", line: 2.5, side: "over", lean: 1, price: -110, p: 0.6, p_mkt: 0.52, edge: 0.08,
  ev: 0.1, conf: 70, pi: 80, result: "win", profit: 0.909, close: -130, clv: 0.03, version: "1.4", synthetic: 0, ...o,
});

describe("backtest", () => {
  it("filters on frozen values only and takes one bet per prop at the best qualifying price", () => {
    const rows = [
      r({ book: "FanDuel", price: -120 }),
      r({ book: "BetMGM", price: -105 }),
      r({ player_id: 2, player: "B", p: 0.52, edge: 0.02, result: "loss" }),
    ];
    const all = bets(rows, BT_DEFAULT);
    expect(all).toHaveLength(2);
    expect(all.find((b) => b.player === "A")!.book).toBe("BetMGM");
    expect(bets(rows, { ...BT_DEFAULT, minProb: 55 }).map((b) => b.player)).toEqual(["A"]);
    expect(bets(rows, { ...BT_DEFAULT, minEdge: 5 }).map((b) => b.player)).toEqual(["A"]);
    expect(bets(rows, { ...BT_DEFAULT, book: "FanDuel" })[0]!.price).toBe(-120);
  });
  it("record, units, ROI, CLV in cents and drawdown", () => {
    const s = summarize([
      { r: r({ result: "win", price: -110, close: -135 }) },
      { r: r({ result: "loss", price: -110, close: -105 }) },
      { r: r({ result: "loss", price: 150, close: null }) },
      { r: r({ result: "push" }) },
      { r: r({ result: "void" }) },
    ]);
    expect([s.wins, s.losses, s.pushes, s.voids]).toEqual([1, 2, 1, 1]);
    expect(s.units).toBeCloseTo(100 / 110 - 2, 10);
    expect(s.roi).toBeCloseTo((100 / 110 - 2) / 4, 10);
    expect(s.clv_cents).toBeCloseTo((25 - 5 + 20) / 3, 10); // the push (-110, closed -130) counts too
    expect(s.drawdown).toBeCloseTo(2, 10);
    expect(s.win_pct).toBeCloseTo(1 / 3, 10);
  });
  it("CLV by group needs enough bets", () => {
    const rows = Array.from({ length: 6 }, () => r({}));
    expect(clvBy(rows, "book")).toEqual([{ key: "FanDuel", n: 6, clv_cents: 20, clv_ev: 0.03 }]);
    expect(clvBy(rows.slice(0, 3), "book")).toEqual([]);
  });
});
