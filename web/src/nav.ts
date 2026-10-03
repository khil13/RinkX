// The site map: one entry per page, with the roadmap phase that delivers it (docs/08-roadmap.md).

export interface NavItem {
  to: string;
  label: string;
  phase: number; // roadmap phase that delivers the page (docs/08-roadmap.md)
}

export const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", phase: 0 },
  { to: "/games", label: "Games", phase: 1 },
  { to: "/props", label: "Props", phase: 5 },
  { to: "/props/best", label: "Best Props", phase: 6 },
  { to: "/players", label: "Players", phase: 1 },
  { to: "/models", label: "Model Tests", phase: 3 },
  { to: "/goalies", label: "Goalies", phase: 10 },
  { to: "/lines", label: "Line Movement", phase: 10 },
  { to: "/parlay", label: "Parlay Builder", phase: 8 },
  { to: "/performance", label: "Model Performance", phase: 7 },
  { to: "/news", label: "News", phase: 8 },
  { to: "/settings", label: "Settings", phase: 9 },
  { to: "/admin", label: "Admin", phase: 0 },
];

// Pages that exist; the rest show their roadmap phase in the nav.
export const BUILT_ROUTES = new Set(["/", "/games", "/props", "/props/best", "/players", "/models", "/performance", "/news", "/parlay", "/settings", "/goalies", "/lines", "/admin"]);
