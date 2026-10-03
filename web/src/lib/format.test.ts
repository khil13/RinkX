import { describe, expect, it } from "vitest";
import { cents } from "../components/Lines";
import { additiveSteps } from "../components/Projections";
import { ago, githubRepo, longDate, minutesSince, mmss, seasonLabel, signed, wilson } from "./format";

const now = Date.parse("2026-10-10T18:00:00Z");

describe("format", () => {
  it("formats relative times", () => {
    expect(ago("2026-10-10T18:00:00Z", now)).toBe("just now");
    expect(ago("2026-10-10T17:53:00Z", now)).toBe("7 min ago");
    expect(ago("2026-10-10T15:00:00Z", now)).toBe("3 h ago");
    expect(ago("2026-10-07T18:00:00Z", now)).toBe("3 d ago");
    expect(minutesSince("2026-10-10T19:00:00Z", now)).toBe(0);
  });

  it("derives the repo from a GitHub Pages URL", () => {
    expect(githubRepo({ hostname: "khil13.github.io", pathname: "/RinkX/" } as Location)).toEqual({
      owner: "khil13",
      repo: "RinkX",
    });
    expect(githubRepo({ hostname: "localhost", pathname: "/" } as Location)).toBeNull();
  });
});

describe("stat formatting", () => {
  it("formats TOI and plus-minus without inventing values", () => {
    expect(mmss(1024)).toBe("17:04");
    expect(mmss(0)).toBe("0:00");
    expect(mmss(null)).toBe("—");
    expect(signed(2)).toBe("+2");
    expect(signed(-1)).toBe("-1");
    expect(signed(0)).toBe("0");
    expect(signed(null)).toBe("—");
  });

  it("keeps calendar dates on their own day", () => {
    expect(longDate("2026-03-10")).toMatch(/10/);
  });
});

describe("hit-rate helpers", () => {
  it("matches the Python Wilson interval", () => {
    const [lo, hi] = wilson(4, 5)!;
    expect(lo).toBeCloseTo(0.376, 3);
    expect(hi).toBeCloseTo(0.964, 3);
    expect(wilson(0, 0)).toBeNull();
  });
  it("labels seasons", () => {
    expect(seasonLabel(20262027)).toBe("2026-27");
    expect(seasonLabel(null)).toBe("");
  });
});


describe("line movement in cents", () => {
  it("counts cents across even money", () => {
    expect(cents(-105, -125)).toBe(20);
    expect(cents(105, -105)).toBe(10);
    expect(cents(-125, -105)).toBe(-20);
  });
});

describe("view calculation", () => {
  it("additive steps end at the multiplicative projection", () => {
    const steps = additiveSteps(3.2, [
      { name: "Ice time", effect: 0.1 },
      { name: "Opponent", effect: -0.05 },
    ]);
    expect(steps[steps.length - 1]!.total).toBeCloseTo(3.2 * 1.1 * 0.95, 12);
    expect(steps[0]!.delta).toBeCloseTo(0.32, 12);
  });
});
