import { describe, expect, it } from "vitest";
import { ago, githubRepo, longDate, minutesSince, mmss, signed } from "./format";

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
