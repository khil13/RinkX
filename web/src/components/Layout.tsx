import { useState } from "react";
import { NavLink, Outlet } from "react-router";
import { useManifest } from "../lib/data/fetch";
import { ago, minutesSince, SITE_STALE_MINUTES } from "../lib/format";
import { useSession } from "../lib/session";
import { DataChip } from "./ui";

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
  { to: "/goalies", label: "Goalies", phase: 4 },
  { to: "/lines", label: "Line Movement", phase: 4 },
  { to: "/parlay", label: "Parlay Builder", phase: 8 },
  { to: "/performance", label: "Model Performance", phase: 7 },
  { to: "/news", label: "News", phase: 8 },
  { to: "/settings", label: "Settings", phase: 9 },
  { to: "/admin", label: "Admin", phase: 0 },
];

const TABS = ["/", "/games", "/props/best", "/players"];
const exact = (to: string) => to === "/" || to === "/props";
// Pages that exist; the rest show their roadmap phase in the nav.
export const BUILT_ROUTES = new Set(["/", "/games", "/props", "/props/best", "/players", "/models", "/performance", "/news", "/parlay", "/admin"]);

function navClass({ isActive }: { isActive: boolean }) {
  return `flex items-center justify-between rounded-md px-3 py-2 text-sm ${
    isActive ? "bg-panel-2 text-text" : "text-muted hover:bg-panel-2 hover:text-text"
  }`;
}

function Freshness() {
  const manifest = useManifest().data;
  if (!manifest) return null;
  const stale = minutesSince(manifest.generated_at) > SITE_STALE_MINUTES;
  return <DataChip state={stale ? "stale" : "fresh"} detail={ago(manifest.generated_at)} />;
}

export function Layout() {
  const manifest = useManifest().data;
  const { lock } = useSession();
  const [moreOpen, setMoreOpen] = useState(false);

  return (
    <div className="flex min-h-full flex-col">
      {manifest?.env === "dev" && (
        <div className="bg-warn px-4 py-1 text-center text-xs font-bold text-bg" role="alert">
          DEV BUILD · may contain SYNTHETIC data · not real lines, stats or projections
        </div>
      )}
      <header className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-line bg-bg/95 px-4 py-3 backdrop-blur">
        <div className="flex items-center gap-2">
          <span className="text-base font-bold tracking-widest text-accent">RINKX</span>
          <span className="hidden text-xs text-muted sm:inline">NHL props research</span>
        </div>
        <div className="flex items-center gap-2">
          <Freshness />
          <button onClick={() => void lock()} className="min-h-9 rounded-md px-2 text-xs text-muted hover:text-text">
            Lock
          </button>
        </div>
      </header>

      <div className="flex flex-1">
        <nav className="hidden w-56 shrink-0 flex-col gap-0.5 border-r border-line p-3 md:flex" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={exact(n.to)} className={navClass}>
              {n.label}
              {!BUILT_ROUTES.has(n.to) && <span className="num text-[10px] text-muted/60">P{n.phase}</span>}
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1 px-4 pb-28 pt-4 md:pb-8">
          <Outlet />
        </main>
      </div>

      <footer className="hidden border-t border-line px-4 py-3 text-xs text-muted md:block">
        Statistical estimates, not guarantees. Bet only what you can afford to lose. Gambling problem? Call
        1-800-GAMBLER.
      </footer>

      {/* Mobile bottom tab bar */}
      <nav
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-panel pb-[env(safe-area-inset-bottom)] md:hidden"
        aria-label="Tabs"
      >
        {TABS.map((to) => {
          const item = NAV.find((n) => n.to === to)!;
          return (
            <NavLink
              key={to}
              to={to}
              end={exact(to)}
              onClick={() => setMoreOpen(false)}
              className={({ isActive }) =>
                `flex min-h-14 items-center justify-center text-xs ${isActive ? "text-accent" : "text-muted"}`
              }
            >
              {item.label === "Dashboard" ? "Home" : item.label === "Best Props" ? "Best" : item.label}
            </NavLink>
          );
        })}
        <button
          onClick={() => setMoreOpen((o) => !o)}
          className={`min-h-14 text-xs ${moreOpen ? "text-accent" : "text-muted"}`}
          aria-expanded={moreOpen}
        >
          More
        </button>
      </nav>
      {moreOpen && (
        <div className="fixed inset-x-0 bottom-14 z-30 border-t border-line bg-panel p-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] md:hidden">
          <div className="grid grid-cols-2 gap-1">
            {NAV.filter((n) => !TABS.includes(n.to)).map((n) => (
              <NavLink key={n.to} to={n.to} end={exact(n.to)} onClick={() => setMoreOpen(false)} className={navClass}>
                {n.label}
              </NavLink>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-muted">
            Statistical estimates, not guarantees. Gambling problem? Call 1-800-GAMBLER.
          </p>
        </div>
      )}
    </div>
  );
}
