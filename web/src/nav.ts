// The site map: a few top-level sections, each with its pages as tabs (docs/06-ui.md).

export interface Section {
  label: string;
  /** the section's pages; the first is where the section link goes */
  tabs: { to: string; label: string }[];
}

export const SECTIONS: Section[] = [
  { label: "Today", tabs: [{ to: "/", label: "Today" }] },
  { label: "Card of the Day", tabs: [{ to: "/props/best", label: "Card of the Day" }] },
  {
    label: "Props",
    tabs: [
      { to: "/props", label: "All props" },
      { to: "/lines", label: "Line movement" },
    ],
  },
  { label: "Games", tabs: [{ to: "/games", label: "Games" }] },
  { label: "Players", tabs: [{ to: "/players", label: "Players" }] },
  {
    label: "Lineups",
    tabs: [
      { to: "/goalies", label: "Goalies" },
      { to: "/deployment", label: "Lines & PP" },
      { to: "/news", label: "News & alerts" },
    ],
  },
  { label: "Parlay", tabs: [{ to: "/parlay", label: "Parlay" }] },
  {
    label: "Results",
    tabs: [
      { to: "/performance", label: "Performance" },
      { to: "/backtest", label: "Backtest" },
      { to: "/my", label: "My bets" },
      { to: "/models", label: "Model tests" },
    ],
  },
  {
    label: "Settings",
    tabs: [
      { to: "/settings", label: "Settings" },
      { to: "/admin", label: "Data & admin" },
    ],
  },
];

/** The section a path belongs to (deeper paths like /games/123 belong to their parent). */
export function sectionOf(path: string): Section | undefined {
  const exact = SECTIONS.find((s) => s.tabs.some((t) => t.to === path));
  if (exact) return exact;
  return SECTIONS.find((s) => s.tabs.some((t) => t.to !== "/" && path.startsWith(`${t.to}/`)));
}

/** Old addresses that moved, so bookmarks keep working. */
export const REDIRECTS: Record<string, string> = { "/today": "/" };
