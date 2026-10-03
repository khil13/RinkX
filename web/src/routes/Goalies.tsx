import { Link } from "react-router";
import { GoalieLine } from "../components/Projections";
import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted, useManifest } from "../lib/data/fetch";
import type { GameSummary, Slate } from "../lib/data/types";
import { clock, longDate } from "../lib/format";

function nextDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = new Date(Date.UTC(y!, m! - 1, d! + 1));
  return t.toISOString().slice(0, 10);
}

function Day({ date }: { date: string }) {
  const slate = useEncrypted<Slate>(`slate/${date}.json`);
  if (slate.state === "loading") return <Spinner label="Loading goalies…" />;
  if (slate.state !== "ready") return <p className="text-sm text-muted">No schedule published for {longDate(date)}.</p>;
  const games = slate.value.data.games.filter((g) => g.status !== "postponed" && g.status !== "cancelled");
  if (games.length === 0) return <p className="text-sm text-muted">No NHL games on {longDate(date)}.</p>;
  const sides = games.flatMap((g) => [g.away, g.home]);
  const confirmed = sides.filter((s) => s.goalie?.status === "confirmed" || s.goalie?.status === "actual").length;
  return (
    <>
      <p className="mb-2 text-xs text-muted">
        {confirmed} of {sides.length} starters confirmed or known from the box score.
      </p>
      <ul className="flex flex-col divide-y divide-line" aria-label={`Goalies ${date}`}>
        {games.map((g: GameSummary) => (
          <li key={g.id} className="flex flex-col gap-1.5 py-2">
            <Link to={`/games/${g.id}`} className="num text-xs text-muted hover:text-accent">
              {g.away.team.abbrev} @ {g.home.team.abbrev} · {clock(g.start_time_utc)}
            </Link>
            {[g.away, g.home].map((s) => (
              <div key={s.team.abbrev} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="num w-10 text-xs text-muted">{s.team.abbrev}</span>
                <GoalieLine g={s.goalie} gameId={g.id} team={s.team.abbrev} />
              </div>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}

export function Goalies() {
  const manifest = useManifest().data;
  if (!manifest) return <Spinner label="Loading…" />;
  const today = manifest.slate_date;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">Goalies</h1>
        <p className="text-sm text-muted">
          Starters for today and tomorrow. <em>Confirmed</em> means you entered it through Quick Entry with a
          source; <em>projected</em> comes from each team's recent starts, with the share shown. There is no automatic
          confirmation feed.
        </p>
      </header>
      {!today ? (
        <Notice tone="warn">No schedule published yet.</Notice>
      ) : (
        <>
          <Panel title={`Today · ${longDate(today)}`}>
            <Day date={today} />
          </Panel>
          <Panel title={`Tomorrow · ${longDate(nextDay(today))}`}>
            <Day date={nextDay(today)} />
          </Panel>
        </>
      )}
    </div>
  );
}
