import { describe, expect, it } from "vitest";
import { coolOffActive, getSettings, odds, updateSettings } from "./settings";

describe("settings", () => {
  it("formats odds as American or decimal", () => {
    expect(odds(-110, "american")).toBe("−110");
    expect(odds(150, "american")).toBe("+150");
    expect(odds(-110, "decimal")).toBe("1.91");
    expect(odds(150, "decimal")).toBe("2.50");
    expect(odds(null)).toBe("—");
  });

  it("a cool-off can be extended but never shortened or ended early", () => {
    const now = Date.now();
    const day = new Date(now + 24 * 3600_000).toISOString();
    updateSettings({ coolOffUntil: day });
    expect(coolOffActive()).toBe(true);
    updateSettings({ coolOffUntil: null });
    expect(getSettings().coolOffUntil).toBe(day);
    updateSettings({ coolOffUntil: new Date(now + 3600_000).toISOString() });
    expect(getSettings().coolOffUntil).toBe(day);
    const week = new Date(now + 7 * 24 * 3600_000).toISOString();
    updateSettings({ coolOffUntil: week });
    expect(getSettings().coolOffUntil).toBe(week);
    expect(coolOffActive(getSettings(), now + 8 * 24 * 3600_000)).toBe(false);
    updateSettings({ odds: "decimal" }); // other settings still change during a cool-off
    expect(getSettings().odds).toBe("decimal");
  });
});
