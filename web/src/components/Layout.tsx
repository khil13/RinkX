import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { useManifest } from "../lib/data/fetch";
import { ago, minutesSince, SITE_STALE_MINUTES } from "../lib/format";
import { useParlayLegs } from "../lib/parlayStore";
import { useSession } from "../lib/session";
import { BUILT_ROUTES, NAV } from "../nav";
import { DataChip } from "./ui";

// Phone tab bar (docs/06-ui.md): Slate · Card (of the Day) · Search · Parlay · More.
const TABS: [string, string][] = [
  ["/games", "Slate"],
  ["/props/best", "Card"],
  ["/players", "Search"],
  ["/parlay", "Parlay"],
];
const TAB_PATHS = TABS.map(([to]) => to);
const exact = (to: string) => to === "/" || to === "/props";

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

function MoreSheet({ onClose, onLock }: { onClose: () => void; onLock: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 md:hidden">
      <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div
        id="more-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="More"
        className="sheet-in absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-line bg-panel px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-2"
      >
        <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-line" aria-hidden />
        <div className="grid grid-cols-2 gap-1">
          {NAV.filter((n) => !TAB_PATHS.includes(n.to)).map((n) => (
            <NavLink key={n.to} to={n.to} end={exact(n.to)} onClick={onClose} className={sheetLinkClass}>
              {n.label === "Dashboard" ? "Home" : n.label}
              {!BUILT_ROUTES.has(n.to) && <span className="num text-[10px] text-muted/60">P{n.phase}</span>}
            </NavLink>
          ))}
          <button type="button" onClick={onLock} className="flex min-h-12 items-center rounded-md px-3 text-sm text-muted">
            Lock
          </button>
        </div>
        <p className="mt-3 text-[11px] text-muted">
          Statistical estimates, not guarantees. Gambling problem? Call 1-800-GAMBLER.
        </p>
      </div>
    </div>
  );
}

function sheetLinkClass({ isActive }: { isActive: boolean }) {
  return `flex min-h-12 items-center justify-between rounded-md px-3 text-sm ${
    isActive ? "bg-panel-2 text-text" : "text-muted hover:bg-panel-2 hover:text-text"
  }`;
}

export function Layout() {
  const manifest = useManifest().data;
  const { lock } = useSession();
  const [moreOpen, setMoreOpen] = useState(false);
  const legs = useParlayLegs();

  return (
    <div className="flex min-h-full flex-col">
      {manifest?.env === "dev" && (
        <div className="bg-warn px-4 py-1 text-center text-xs font-bold text-bg" role="alert">
          DEV BUILD · may contain SYNTHETIC data · not real lines, stats or projections
        </div>
      )}
      <header className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-line bg-bg/95 px-4 pb-3 pt-[max(env(safe-area-inset-top),0.75rem)] backdrop-blur">
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
        {TABS.map(([to, label]) => (
          <NavLink
            key={to}
            to={to}
            end={exact(to)}
            onClick={() => setMoreOpen(false)}
            className={({ isActive }) =>
              `relative flex min-h-14 items-center justify-center text-xs ${isActive ? "text-accent" : "text-muted"}`
            }
          >
            {label}
            {to === "/parlay" && legs.length > 0 && (
              <span className="num absolute right-[22%] top-2 rounded-full bg-accent px-1 text-[10px] font-bold leading-4 text-bg">
                {legs.length}
              </span>
            )}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setMoreOpen((o) => !o)}
          className={`min-h-14 text-xs ${moreOpen ? "text-accent" : "text-muted"}`}
          aria-expanded={moreOpen}
          aria-controls="more-sheet"
        >
          More
        </button>
      </nav>
      {moreOpen && <MoreSheet onClose={() => setMoreOpen(false)} onLock={() => void lock()} />}
    </div>
  );
}
