import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { Missing, Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { GoalieGame, PlayerIndexEntry, PlayerPage, SkaterGame } from "../lib/data/types";
import { longDate, mmss, num, signed } from "../lib/format";

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

function SkaterLog({ games }: { games: SkaterGame[] }) {
  return (
    <table className="num w-full min-w-[560px] text-sm">
      <thead className="text-xs text-muted">
        <tr>
          {["Date", "Opp", "TOI", "G", "A", "P", "SOG", "HIT", "BLK", "PIM", "+/-"].map((h, i) => (
            <th key={h} className={`py-1 font-normal ${i < 2 ? "text-left" : "text-right"}`}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {games.map((g) => (
          <tr key={g.game_id} className="border-t border-line">
            <td className="py-1">{longDate(g.date)}</td>
            <td>{g.home ? "vs" : "@"} {g.opponent}</td>
            <td className="text-right">{mmss(g.toi_s)}</td>
            <td className="text-right">{num(g.g)}</td>
            <td className="text-right">{num(g.a)}</td>
            <td className="text-right">{num(g.p)}</td>
            <td className="text-right">{num(g.sog)}</td>
            <td className="text-right">{num(g.hits)}</td>
            <td className="text-right">{num(g.blk)}</td>
            <td className="text-right">{num(g.pim)}</td>
            <td className="text-right">{signed(g.pm)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function GoalieLog({ games }: { games: GoalieGame[] }) {
  return (
    <table className="num w-full min-w-[480px] text-sm">
      <thead className="text-xs text-muted">
        <tr>
          {["Date", "Opp", "GS", "TOI", "SA", "SV", "GA", "Dec"].map((h, i) => (
            <th key={h} className={`py-1 font-normal ${i < 2 ? "text-left" : "text-right"}`}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {games.map((g) => (
          <tr key={g.game_id} className="border-t border-line">
            <td className="py-1">{longDate(g.date)}</td>
            <td>{g.home ? "vs" : "@"} {g.opponent}</td>
            <td className="text-right">{g.started ? "✓" : ""}</td>
            <td className="text-right">{mmss(g.toi_s)}</td>
            <td className="text-right">{num(g.sa)}</td>
            <td className="text-right">{num(g.sv)}</td>
            <td className="text-right">{num(g.ga)}</td>
            <td className="text-right">{g.decision === "O" ? "OTL" : (g.decision ?? "")}</td>
          </tr>
        ))}
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
  ["ppg", "PPG"],
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

  const { player: p, totals, totals_reason, games, season } = res.value.data;
  const goalie = p.position === "G";
  const cells = goalie ? GOALIE_TOTALS : SKATER_TOTALS;

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

      <Panel title={`Season ${season ? `${String(season).slice(0, 4)}-${String(season).slice(6)}` : ""}`}>
        {totals ? (
          <dl className="grid grid-cols-4 gap-3 sm:grid-cols-8">
            {cells.map(([k, label]) => (
              <div key={k}>
                <dt className="text-xs text-muted">{label}</dt>
                <dd className="num text-lg">
                  {k === "sv_pct" && totals[k] !== null ? (totals[k] as number).toFixed(3).replace(/^0/, "") : num(totals[k])}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <Missing reason={totals_reason} />
        )}
        {!goalie && totals && (
          <p className="mt-2 text-xs text-muted">
            Avg TOI {mmss((totals.toi_avg_s as number | null) ?? null)} · PP points need play-by-play (Phase 2).
          </p>
        )}
      </Panel>

      <Panel title={`Game log (${games.length})`}>
        {games.length === 0 ? (
          <p className="text-sm text-muted">No games with box scores this season yet.</p>
        ) : (
          <div className="overflow-x-auto">
            {goalie ? <GoalieLog games={games as GoalieGame[]} /> : <SkaterLog games={games as SkaterGame[]} />}
          </div>
        )}
        <p className="mt-2 text-xs text-muted">Every game is listed. Nothing is filtered.</p>
      </Panel>
    </div>
  );
}
