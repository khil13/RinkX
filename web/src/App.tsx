import { lazy, Suspense } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router";
import { Notice, Spinner } from "./components/ui";
import { REDIRECTS } from "./nav";
import { useManifest } from "./lib/data/fetch";
import { SessionProvider, useSession } from "./lib/session";
import { Setup } from "./routes/Setup";
import { Unlock } from "./routes/Unlock";
import { useSettings } from "./lib/settings";

// Every signed-in page loads on demand: the unlock screen downloads only what it needs.
const named = <K extends string>(load: () => Promise<Record<K, React.ComponentType<any>>>, key: K) =>
  lazy(() => load().then((m) => ({ default: m[key] })));
const Layout = named(() => import("./components/Layout"), "Layout");
const Games = named(() => import("./routes/Games"), "Games");
const Game = named(() => import("./routes/Game"), "Game");
const Players = named(() => import("./routes/Players"), "Players");
const Player = named(() => import("./routes/Players"), "Player");
const CoolOffGate = named(() => import("./routes/Settings"), "CoolOffGate");
const BestProps = named(() => import("./routes/BestProps"), "BestProps");
const CardOfDay = named(() => import("./routes/CardOfDay"), "CardOfDay");
const Today = named(() => import("./routes/Today"), "Today");
const Backtest = named(() => import("./routes/Backtest"), "Backtest");
const MyPerformance = named(() => import("./routes/MyPerformance"), "MyPerformance");
const DeploymentPage = named(() => import("./routes/DeploymentPage"), "DeploymentPage");
const News = named(() => import("./routes/News"), "News");
const Parlay = named(() => import("./routes/Parlay"), "Parlay");
const Performance = named(() => import("./routes/Performance"), "Performance");
const Models = named(() => import("./routes/Models"), "Models");
const Admin = named(() => import("./routes/Admin"), "Admin");
const Goalies = named(() => import("./routes/Goalies"), "Goalies");
const LineMovement = named(() => import("./routes/LineMovement"), "LineMovement");
const SettingsPage = named(() => import("./routes/Settings"), "Settings");

const page = (el: React.ReactElement) => <Suspense fallback={<Spinner label="Loading…" />}>{el}</Suspense>;
const priced = (el: React.ReactElement) => page(<CoolOffGate>{el}</CoolOffGate>);

// Every page, by path (the menu and its tabs are in nav.ts).
const PAGES: Record<string, () => React.ReactElement> = {
  "/": () => priced(<Today />),
  "/games": () => page(<Games />),
  "/players": () => page(<Players />),
  "/props": () => priced(<BestProps all />),
  "/props/best": () => priced(<CardOfDay />),
  "/backtest": () => priced(<Backtest />),
  "/my": () => priced(<MyPerformance />),
  "/deployment": () => <DeploymentPage />,
  "/models": () => page(<Models />),
  "/performance": () => page(<Performance />),
  "/news": () => page(<News />),
  "/parlay": () => priced(<Parlay />),
  "/settings": () => page(<SettingsPage />),
  "/goalies": () => page(<Goalies />),
  "/lines": () => priced(<LineMovement />),
  "/admin": () => page(<Admin />),
};

function Gate() {
  const { data: manifest, isPending, isError } = useManifest();
  const { key, restoring } = useSession();
  const settings = useSettings();
  // Odds format and time zone are read while rendering: re-render every page when they change.
  const settingsKey = `${settings.odds}|${settings.timeZone}`;

  if (isPending) return <Spinner label="Loading…" />;
  if (isError || !manifest) {
    return (
      <div className="mx-auto max-w-md p-6">
        <Notice tone="bad">Live data unavailable: the site manifest could not be loaded. Try again shortly.</Notice>
      </div>
    );
  }
  if (!manifest.configured) {
    return (
      <Routes>
        <Route path="*" element={<Setup manifest={manifest} />} />
      </Routes>
    );
  }
  if (restoring) return <Spinner label="Loading…" />;
  return (
    <Routes key={settingsKey}>
      <Route path="/setup" element={<Setup manifest={manifest} />} />
      {!key ? (
        <Route path="*" element={<Unlock />} />
      ) : (
        <Route element={page(<Layout />)}>
          {Object.entries(PAGES).map(([path, el]) => (
            <Route key={path} path={path} element={el()} />
          ))}
          {Object.entries(REDIRECTS).map(([from, to]) => (
            <Route key={from} path={from} element={<Navigate to={to} replace />} />
          ))}
          <Route path="/games/:id" element={page(<Game />)} />
          <Route path="/players/:id" element={page(<Player />)} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      )}
    </Routes>
  );
}

function WithSession() {
  const manifest = useManifest().data;
  return (
    <SessionProvider manifest={manifest}>
      <Gate />
    </SessionProvider>
  );
}

export function App() {
  return (
    <HashRouter>
      <WithSession />
    </HashRouter>
  );
}
