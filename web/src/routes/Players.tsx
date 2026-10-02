import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { Missing, Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import { HitRates } from "../components/HitRates";
import { PlayerLinesPanel } from "../components/Lines";
import { MODEL_REASON_TEXT, ProjectionCard, TestedNote } from "../components/Projections";
import type { DnpGame, GoalieGame, PlayerIndexEntry, PlayerPage, SkaterGame } from "../lib/data/types";
import { longDate, mmss, num, seasonLabel, signed } from "../lib/format";

const MAX_RESULTS = 50;

export function Players() {
  const index = useEncrypted<PlayerIndexEntry[]>("players/index.json");
  const [q, setQ] = useState("");
  const results = useMemo(() => {
    if (index.state !== "ready") return [];
    const needle = q.trim().toLowerCase();
    if (needle.length < 2) return [];
    return index.value.data
      .filter((p) => p.name.toLowerCase().includes(needle) || p.team?.toLowerCase() === needle)
      .slice(0, MAX_RESULTS);
  }, [index, q]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <h1 className="text-lg font-semibold">Players</h1>
      <input
        type="search"
        placeholder="Search by name or team (e.g. TOR)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="min-h-11 rounded-md border border-line bg-panel px-3 text-base outline-none focus:border-accent"
        aria-label="Search players"
      />
      {index.state === "loading" && <Spinner label="Loading players…" />}
      {index.state === "unavailable" && <Notice tone="warn">Live data unavailable: no player list published yet.</Notice>}
      {index.state === "error" && <Notice tone="bad">{index.error.message}</Notice>}
      {index.state === "ready" && q.trim().length >= 2 && (
        <Panel>
          {results.length === 0 ? (
            <p className="text-sm text-muted">No players match.</p>
          ) : (
            <ul className="divide-y divide-line">
              {results.map((p) => (
                <li key={p.id}>
                  <Link to={`/players/${p.id}`} className="flex min-h-11 items-center justify-between gap-3 py-2">
                    <span>{p.name}</span>
                    <span className="num text-xs text-muted">
                      {p.team ?? "No team"} · {p.position}
                      {p.number !== null && ` · #${p.number}`}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}
      {index.state === "ready" && (
        <p className="text-xs text-muted">{index.value.data.length.toLocaleString()} players on current NHL rosters.</p>
      )}
    </div>
  );
}

type LogRow = SkaterGame | GoalieGame | DnpGame;

function DnpRow({ g, cols }: { g: DnpGame; cols: number }) {
  return (
    <tr className="border-t border-line text-muted">
      <td className="py-1">{longDate(g.date)}</td>
      <td>
        {g.home ? "vs" : "@"} {g.opponent}
      </td>
      <td colSpan={cols} className="text-right text-xs italic">
        Did not play (not counted in hit rates)
      </td>
    </tr>
  );
}

const SKATER_COLS = ["TOI", "PP", "G", "A", "P", "SOG", "iCF", "HIT", "BLK", "PPP", "+/-"];

function SkaterLog({ games }: { games: LogRow[] }) {
  return (
    <table className="num w-full min-w-[640px] text-sm">
      <thead className="text-xs text-muted">
        <tr>
          {["Date", "Opp", ...SKATER_COLS].map((h, i) => (
            <th key={h} className={`py-1 font-normal ${i < 2 ? "text-left" : "text-right"}`}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {games.map((row) => {
          if (row.dnp) return <DnpRow key={row.game_id} g={row} cols={SKATER_COLS.length} />;
          const g = row as SkaterGame;
          return (
            <tr key={g.game_id} className="border-t border-line">
              <td className="py-1">
                {longDate(g.date)}
                {g.playoffs && <span className="ml-1 text-[10px] text-muted">PO</span>}
              </td>
              <td>
                {g.home ? "vs" : "@"} {g.opponent}
              </td>
              <td className="text-right">{mmss(g.toi_s)}</td>
              <td className="text-right text-muted">{mmss(g.pp_toi_s)}</td>
              <td className="text-right">{num(g.g)}</td>
              <td className="text-right" title={g.a1 !== null ? `${g.a1} primary, ${g.a2} secondary` : undefined}>
                {num(g.a)}
              </td>
              <td className="text-right">{num(g.p)}</td>
              <td className="text-right">{num(g.sog)}</td>
              <td className="text-right">{num(g.icf)}</td>
              <td className="text-right">{num(g.hits)}</td>
              <td className="text-right">{num(g.blk)}</td>
              <td className="text-right">{num(g.ppp)}</td>
              <td className="text-right">{signed(g.pm)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const GOALIE_COLS = ["GS", "TOI", "SA", "SV", "GA", "Dec"];

function GoalieLog({ games }: { games: LogRow[] }) {
  return (
    <table className="num w-full min-w-[480px] text-sm">
      <thead className="text-xs text-muted">
        <tr>
          {["Date", "Opp", ...GOALIE_COLS].map((h, i) => (
            <th key={h} className={`py-1 font-normal ${i < 2 ? "text-left" : "text-right"}`}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {games.map((row) => {
          if (row.dnp) return <DnpRow key={row.game_id} g={row} cols={GOALIE_COLS.length} />;
          const g = row as GoalieGame;
          return (
            <tr key={g.game_id} className="border-t border-line">
              <td className="py-1">{longDate(g.date)}</td>
              <td>
                {g.home ? "vs" : "@"} {g.opponent}
              </td>
              <td className="text-right">{g.started ? "✓" : ""}</td>
              <td className="text-right">{mmss(g.toi_s)}</td>
              <td className="text-right">{num(g.sa)}</td>
              <td className="text-right">{num(g.sv)}</td>
              <td className="text-right">{num(g.ga)}</td>
              <td className="text-right">{g.decision === "O" ? "OTL" : (g.decision ?? "")}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const SKATER_TOTALS: [string, string][] = [
  ["gp", "GP"],
  ["g", "G"],
  ["a", "A"],
  ["p", "P"],
  ["sog", "SOG"],
  ["hits", "Hits"],
  ["blk", "Blocks"],
  ["ppp", "PPP"],
];
const GOALIE_TOTALS: [string, string][] = [
  ["gp", "GP"],
  ["starts", "GS"],
  ["w", "W"],
  ["l", "L"],
  ["otl", "OTL"],
  ["sv_pct", "SV%"],
  ["gaa", "GAA"],
  ["so", "SO"],
];

export function Player() {
  const { id } = useParams();
  const res = useEncrypted<PlayerPage>(`players/${id}.json`);

  if (res.state === "loading") return <Spinner label="Loading player…" />;
  if (res.state === "error") return <Notice tone="bad">{res.error.message}</Notice>;
  if (res.state === "unavailable") return <Notice tone="warn">Live data unavailable for this player.</Notice>;

  const {
    player: p,
    totals,
    totals_reason,
    games,
    season,
    last_season,
    last_season_games,
    hit_rates,
    hit_rates_basis,
    projection,
    lines,
  } = res.value.data;
  const goalie = p.position === "G";
  const cells = goalie ? GOALIE_TOTALS : SKATER_TOTALS;
  const played = games.filter((g) => !g.dnp).length;
  const Log = goalie ? GoalieLog : SkaterLog;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">{p.name}</h1>
        <p className="num text-sm text-muted">
          {p.team ?? "No current team"} · {p.position}
          {p.number !== null && ` · #${p.number}`}
          {p.shoots_catches && ` · ${goalie ? "Catches" : "Shoots"} ${p.shoots_catches}`}
          {p.height_cm && ` · ${p.height_cm} cm`}
          {p.weight_kg && ` · ${p.weight_kg} kg`}
          {p.birth_date && ` · born ${p.birth_date}`}
        </p>
      </header>

      <Panel title={`Season ${seasonLabel(season)}`}>
        {totals ? (
          <dl className="grid grid-cols-4 gap-3 sm:grid-cols-8">
            {cells.map(([k, label]) => (
              <div key={k}>
                <dt className="text-xs text-muted">{label}</dt>
                <dd className="num text-lg">
                  {k === "sv_pct" && totals[k] !== null
                    ? (totals[k] as number).toFixed(3).replace(/^0/, "")
                    : num(totals[k])}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <Missing reason={totals_reason} />
        )}
        {!goalie && totals && (
          <p className="num mt-2 text-xs text-muted">
            Avg TOI {mmss((totals.toi_avg_s as number | null) ?? null)} · PP TOI{" "}
            {mmss((totals.pp_toi_avg_s as number | null) ?? null)} per game
          </p>
        )}
      </Panel>

      <Panel
        title={
          projection.game
            ? `Projection · ${projection.game.home ? "vs" : "@"} ${projection.game.opponent} · ${longDate(projection.game.date)}`
            : "Projection"
        }
      >
        {projection.game ? (
          <div className="flex flex-col gap-3">
            <div className="grid gap-3 sm:grid-cols-2">
              {projection.markets.map((m) => (
                <ProjectionCard key={m.market} m={m} />
              ))}
            </div>
            <TestedNote testedAt={projection.models.tested_at} />
          </div>
        ) : (
          <p className="text-sm text-muted">{MODEL_REASON_TEXT[projection.reason ?? "no_upcoming_projection"]}</p>
        )}
      </Panel>

      {lines && lines.markets.length > 0 && (
        <Panel title={`Sportsbook lines · ${longDate(lines.game.date)}`}>
          <PlayerLinesPanel markets={lines.markets} />
        </Panel>
      )}

      <Panel title="Hit rates">
        {Object.keys(hit_rates).length ? (
          <HitRates rates={hit_rates} basis={hit_rates_basis} />
        ) : (
          <Missing reason="insufficient_sample" />
        )}
      </Panel>

      <Panel title={`Game log ${seasonLabel(season)} (${played} played)`}>
        {games.length === 0 ? (
          <p className="text-sm text-muted">No games with box scores this season yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <Log games={games} />
          </div>
        )}
        <p className="mt-2 text-xs text-muted">Every game is listed, including games missed. Nothing is filtered.</p>
      </Panel>

      {last_season_games.length > 0 && (
        <details className="rounded-lg border border-line bg-panel">
          <summary className="cursor-pointer px-4 py-3 text-xs font-semibold uppercase tracking-wider text-muted">
            Game log {seasonLabel(last_season)} ({last_season_games.length} played)
          </summary>
          <div className="overflow-x-auto px-4 pb-4">
            <Log games={last_season_games} />
          </div>
        </details>
      )}
    </div>
  );
}
