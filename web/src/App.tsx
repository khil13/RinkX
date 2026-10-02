import { HashRouter, Navigate, Route, Routes } from "react-router";
import { Layout, NAV } from "./components/Layout";
import { Notice, Spinner } from "./components/ui";
import { useManifest } from "./lib/data/fetch";
import { SessionProvider, useSession } from "./lib/session";
import { Admin } from "./routes/Admin";
import { Dashboard } from "./routes/Dashboard";
import { Game } from "./routes/Game";
import { BestProps } from "./routes/BestProps";
import { Games } from "./routes/Games";
import { Performance } from "./routes/Performance";
import { Models } from "./routes/Models";
import { Placeholder } from "./routes/Placeholder";
import { Player, Players } from "./routes/Players";
import { Setup } from "./routes/Setup";
import { Unlock } from "./routes/Unlock";

// Keep in sync with BUILT_ROUTES in components/Layout.tsx (the nav's "coming in phase N" tags).
const BUILT: Record<string, () => React.ReactElement> = {
  "/": () => <Dashboard />,
  "/games": () => <Games />,
  "/players": () => <Players />,
  "/props": () => <BestProps all />,
  "/props/best": () => <BestProps />,
  "/models": () => <Models />,
  "/performance": () => <Performance />,
  "/admin": () => <Admin />,
};

function Gate() {
  const { data: manifest, isPending, isError } = useManifest();
  const { key, restoring } = useSession();

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
    <Routes>
      <Route path="/setup" element={<Setup manifest={manifest} />} />
      {!key ? (
        <Route path="*" element={<Unlock />} />
      ) : (
        <Route element={<Layout />}>
          {NAV.map((n) => (
            <Route
              key={n.to}
              path={n.to}
              element={BUILT[n.to]?.() ?? <Placeholder title={n.label} phase={n.phase} />}
            />
          ))}
          <Route path="/games/:id" element={<Game />} />
          <Route path="/players/:id" element={<Player />} />
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
