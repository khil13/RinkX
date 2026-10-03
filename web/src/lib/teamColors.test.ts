import { describe, expect, it } from "vitest";
import { contrastText, TEAM_COLORS, teamColors } from "./teamColors";

describe("team colours", () => {
  it("covers all 32 NHL teams with valid hex colours", () => {
    expect(Object.keys(TEAM_COLORS)).toHaveLength(32);
    for (const [a, b] of Object.values(TEAM_COLORS)) {
      expect(a).toMatch(/^#[0-9A-F]{6}$/);
      expect(b).toMatch(/^#[0-9A-F]{6}$/);
    }
  });
  it("picks readable text for each team's colour", () => {
    expect(contrastText("#FFB81C")).toBe("#000000"); // Boston gold: black text
    expect(contrastText("#00205B")).toBe("#FFFFFF"); // Toronto blue: white text
    expect(teamColors("BOS").text).toBe("#000000");
    expect(teamColors("EDM")).toEqual({ primary: "#041E42", secondary: "#FF4C00", text: "#FFFFFF" });
  });
  it("falls back to neutral colours for unknown teams", () => {
    expect(teamColors("T00").primary).toBe("#3A4556");
    expect(teamColors(null).primary).toBe("#3A4556");
  });
});
