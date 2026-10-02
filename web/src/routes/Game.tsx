import { Link, useParams } from "react-router";
import { Missing, Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { GameDetail, Side } from "../lib/data/types";
import { clock, localTime, longDate, mmss, num, signed } from "../lib/format";
import { RestChip, statusLabel } from "./Games";
import { GameLinesPanel } from "../components/Lines";
import {
  GameOutlook,
  GoalieLine,
  MODEL_REASON_TEXT,
  QuickEntryLink,
  SideProjectionTable,
  TestedNote,
} from "../components/Projections";

function ContextTable({ away, home }: { away: Side; home: Side }) {
  const rows: [string, (s: Side) => string | null][] = [
    ["Record", (s) => (s.record ? `${s.record.w}-${s.record.l}-${s.record.otl} (${s.record.pts} pts)` : null)],
    ["Last 10", (s) => s.record?.l10 ?? null],
    ["Streak", (s) => s.record?.streak ?? null],
    ["Goals for / against", (s) => (s.record ? `${s.record.gf} / ${s.record.ga}` : null)],
    [
      "Rest",
      (s) =>
        s.context
          ? s.context.rest_days === null
            ? s.context.first_game_of_season
              ? "First game of season"
              : null
            : s.context.back_to_back
              ? "Back-to-back"
              : `${s.context.rest_days} day${s.context.rest_days === 1 ? "" : "s"}`
          : null,
    ],
    ["Games in last 7 days", (s) => (s.context ? String(s.context.games_last_7d) : null)],
    [
      "Travel since last game",
      (s) => (s.context?.travel_km != null ? `${Math.round(s.context.travel_km).toLocaleString()} km` : null),
    ],
    ["Time-zone change", (s) => (s.context?.tz_shift_hours != null ? `${signed(s.context.tz_shift_hours)} h` : null)],
  ];
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-xs text-muted">
          <th className="py-1 text-left font-normal" />
          <th className="num py-1 text-right font-semibold text-text">{away.team.abbrev}</th>
          <th className="num py-1 text-right font-semibold text-text">{home.team.abbrev}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, get]) => (
          <tr key={label} className="border-t border-line">
            <td className="py-1.5 pr-2 text-muted">{label}</td>
            {[away, home].map((s) => (
              <td key={s.team.abbrev} className="num py-1.5 text-right">
                {get(s) ?? <Missing reason="data_unavailable" />}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Metrics({ away, home }: { away: Side; home: Side }) {
  if (!away.metrics && !home.metrics) {
    return (
      <p className="text-sm text-muted">
        <Missing reason="insufficient_sample" /> Shown once each team has at least 5 completed games with box
        scores.
      </p>
    );
  }
  const rows: [string, keyof NonNullable<Side["metrics"]>][] = [
    ["Goals for / game", "goals_for_pg"],
    ["Goals against / game", "goals_against_pg"],
    ["Shots for / game", "shots_for_pg"],
    ["Shots against / game", "shots_against_pg"],
    ["Games in sample", "games"],
  ];
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.map(([label, k]) => (
          <tr key={k} className="border-t border-line first:border-0">
            <td className="py-1.5 text-muted">{label}</td>
            {[away, home].map((s) => (
              <td key={s.team.abbrev} className="num py-1.5 text-right">
                {s.metrics ? s.metrics[k] : <Missing reason={s.metrics_reason} />}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Roster({ side }: { side: Side }) {
  if (!side.roster?.length) return <Missing reason="data_unavailable" />;
  const groups: [string, string[]][] = [
    ["Forwards", ["C", "L", "R"]],
    ["Defense", ["D"]],
    ["Goalies", ["G"]],
  ];
  return (
    <div className="flex flex-col gap-3">
      {groups.map(([label, pos]) => (
        <div key={label}>
          <div className="mb-1 text-xs font-semibold text-muted">{label}</div>
          <ul className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
            {side.roster!
              .filter((p) => pos.includes(p.position))
              .map((p) => (
                <li key={p.id} className="flex gap-2 py-0.5 text-sm">
                  <span className="num w-6 text-right text-muted">{p.number ?? ""}</span>
                  <Link className="hover:text-accent" to={`/players/${p.id}`}>
                    {p.name}
                  </Link>
                  <span className="text-xs text-muted">{p.position}</span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Boxscore({ game }: { game: GameDetail }) {
  if (!game.boxscore) return <Missing reason={game.boxscore_reason} />;
  const teams = [game.away.team.abbrev, game.home.team.abbrev];
  return (
    <div className="flex flex-col gap-5">
      {teams.map((abbrev) => (
        <div key={abbrev} className="overflow-x-auto">
          <div className="mb-1 text-xs font-semibold text-muted">{abbrev}</div>
          <table className="num w-full min-w-[520px] text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-1 text-left font-normal">Skater</th>
                {["TOI", "G", "A", "P", "SOG", "HIT", "BLK", "PIM", "+/-"].map((h) => (
                  <th key={h} className="py-1 text-right font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {game.boxscore!.skaters
                .filter((s) => s.team === abbrev)
                .map((s) => (
                  <tr key={s.id} className="border-t border-line">
                    <td className="py-1 pr-2 font-sans">
                      <Link className="hover:text-accent" to={`/players/${s.id}`}>
                        {s.name}
                      </Link>{" "}
                      <span className="text-xs text-muted">{s.position}</span>
                    </td>
                    <td className="text-right">{mmss(s.toi_s)}</td>
                    <td className="text-right">{num(s.g)}</td>
                    <td className="text-right">{num(s.a)}</td>
                    <td className="text-right">{num(s.p)}</td>
                    <td className="text-right">{num(s.sog)}</td>
                    <td className="text-right">{num(s.hits)}</td>
                    <td className="text-right">{num(s.blk)}</td>
                    <td className="text-right">{num(s.pim)}</td>
                    <td className="text-right">{signed(s.pm)}</td>
                  </tr>
                ))}
              {game.boxscore!.goalies
                .filter((g) => g.team === abbrev)
                .map((g) => (
                  <tr key={g.id} className="border-t border-line text-muted">
                    <td className="py-1 pr-2 font-sans text-text">
                      <Link className="hover:text-accent" to={`/players/${g.id}`}>
                        {g.name}
                      </Link>{" "}
                      <span className="text-xs">G{g.decision ? ` · ${g.decision === "O" ? "OTL" : g.decision}` : ""}</span>
                    </td>
                    <td className="text-right">{mmss(g.toi_s)}</td>
                    <td colSpan={8} className="text-right">
                      {num(g.sv)} saves on {num(g.sa)} shots · {num(g.ga)} GA
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ))}
      <p className="text-xs text-muted">
        Ice-time splits (EV/PP/SH), power-play assists and shot attempts aren't in the NHL box score; they arrive
        with play-by-play in Phase 2.
      </p>
    </div>
  );
}

export function Game() {
  const { id } = useParams();
  const res = useEncrypted<GameDetail>(`games/${id}.json`);

  if (res.state === "loading") return <Spinner label="Loading game…" />;
  if (res.state === "error") return <Notice tone="bad">{res.error.message}</Notice>;
  if (res.state === "unavailable") {
    return (
      <Notice tone="warn">
        Live data unavailable for this game. Only games within three days of today are published.{" "}
        <Link className="underline" to="/games">
          Back to games
        </Link>
      </Notice>
    );
  }
  const g = res.value.data;
  const done = g.status === "final" || g.status === "live";

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <Link to={`/games${g.date ? `?date=${g.date}` : ""}`} className="text-xs text-muted hover:text-text">
        ← {longDate(g.date)}
      </Link>
      <header className="rounded-lg border border-line bg-panel p-4">
        <div className="flex items-center justify-between text-xs text-muted">
          <span className="num font-semibold text-text">{statusLabel(g)}</span>
          <span>{g.venue ?? "Venue unavailable"}</span>
        </div>
        <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          {[g.away, g.home].map((s, i) => (
            <div key={s.team.abbrev} className={i === 0 ? "text-left" : "order-3 text-right"}>
              <div className="num text-2xl font-bold">{s.team.abbrev}</div>
              <div className="text-xs text-muted">
                {s.team.location} {s.team.name}
              </div>
              <div className="mt-1">
                <RestChip side={s} />
              </div>
            </div>
          ))}
          <div className="num order-2 text-center text-2xl font-semibold">
            {done ? `${g.away.score ?? "—"} – ${g.home.score ?? "—"}` : "@"}
          </div>
        </div>
        {!done && <p className="mt-3 text-center text-xs text-muted">Puck drop {clock(g.start_time_utc)}</p>}
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <Panel title="Starting goalies">
          <ul className="flex flex-col gap-2 text-sm">
            {[g.away, g.home].map((s) => (
              <li key={s.team.abbrev} className="flex items-start gap-2">
                <span className="num w-10 shrink-0 text-muted">{s.team.abbrev}</span>
                <GoalieLine g={s.goalie} gameId={g.id} team={s.team.abbrev} />
              </li>
            ))}
          </ul>
          {!done && (
            <p className="mt-2 text-xs text-muted">
              Projected = share of the team's last 10 starts. Confirm a starter with Quick Entry (needs a source
              link).
            </p>
          )}
        </Panel>
        <Panel title="Game outlook (model)">
          {g.projections.environment ? (
            <GameOutlook env={g.projections.environment} away={g.away.team.abbrev} home={g.home.team.abbrev} />
          ) : (
            <p className="text-sm text-muted">
              {done
                ? "Projections are made before the game only."
                : "Not projected: the game model hasn't passed its test on past games yet (see Model Tests)."}
            </p>
          )}
        </Panel>
        <Panel title="Injuries">
          <Missing reason={g.home.injuries_reason} />
        </Panel>
        <Panel title="Lines & power-play units">
          <Missing reason={g.home.lines_reason} />
        </Panel>
      </div>

      {!done && (
        <Panel
          title="Projections"
          right={
            <QuickEntryLink template="player-out" fields={{ game: String(g.id) }}>
              Mark a player out
            </QuickEntryLink>
          }
        >
          {g.projections.reason ? (
            <p className="text-sm text-muted">{MODEL_REASON_TEXT[g.projections.reason]}</p>
          ) : (
            <div className="flex flex-col gap-5">
              <SideProjectionTable side={g.projections.away} abbrev={g.away.team.abbrev} />
              <SideProjectionTable side={g.projections.home} abbrev={g.home.team.abbrev} />
              <p className="text-xs text-muted">
                Lineups aren't confirmed: every rostered skater with NHL history is listed, assuming he dresses.
                Mark scratches with Quick Entry.
              </p>
              <TestedNote testedAt={g.projections.models.tested_at} />
            </div>
          )}
        </Panel>
      )}

      <Panel title="Sportsbook lines">
        <GameLinesPanel lines={g.lines} />
      </Panel>

      <Panel title="Team context">
        <ContextTable away={g.away} home={g.home} />
        <p className="mt-2 text-xs text-muted">
          Records from official NHL standings
          {g.home.record ? ` as of ${g.home.record.as_of}` : ""}. Rest and travel are computed from the schedule.
        </p>
      </Panel>

      <Panel title="Team metrics (season, per game)">
        <Metrics away={g.away} home={g.home} />
      </Panel>

      <Panel title={g.status === "final" ? "Box score" : "Box score (after the game)"}>
        <Boxscore game={g} />
      </Panel>

      <div className="grid gap-3 sm:grid-cols-2">
        {[g.away, g.home].map((s) => (
          <Panel key={s.team.abbrev} title={`${s.team.abbrev} roster`}>
            <Roster side={s} />
          </Panel>
        ))}
      </div>

      <p className="text-xs text-muted">Data fetched {localTime(g.fetched_at)} · Source: NHL public API</p>
    </div>
  );
}
