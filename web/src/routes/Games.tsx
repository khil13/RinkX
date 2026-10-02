import { Link, useSearchParams } from "react-router";
import { DataChip, Missing, Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted, useManifest } from "../lib/data/fetch";
import type { GameSummary, Side, Slate } from "../lib/data/types";
import { clock, longDate } from "../lib/format";

function slateDates(files: Record<string, unknown>): string[] {
  return Object.keys(files)
    .map((f) => f.match(/^slate\/(\d{4}-\d{2}-\d{2})\.json\.enc$/)?.[1])
    .filter((d): d is string => Boolean(d))
    .sort();
}

export function statusLabel(g: GameSummary): string {
  switch (g.status) {
    case "final":
      return g.ended_in && g.ended_in !== "REG" ? `Final/${g.ended_in}` : "Final";
    case "live":
      return g.period ? `Live · P${g.period}` : "Live";
    case "pregame":
      return "Pregame";
    case "postponed":
      return "Postponed";
    case "cancelled":
      return "Cancelled";
    default:
      return clock(g.start_time_utc);
  }
}

export function RestChip({ side }: { side: Side }) {
  const c = side.context;
  if (!c) return <Missing reason={side.context_reason} />;
  if (c.rest_days === null) {
    return <span className="text-xs text-muted">{c.first_game_of_season ? "First game" : "Rest unknown"}</span>;
  }
  return (
    <span className={`num text-xs ${c.back_to_back ? "text-warn" : "text-muted"}`}>
      {c.back_to_back ? "B2B" : `${c.rest_days}d rest`}
      {c.travel_km !== null && c.travel_km > 0 && ` · ${Math.round(c.travel_km).toLocaleString()} km`}
    </span>
  );
}

function TeamRow({ side, done }: { side: Side; done: boolean }) {
  const r = side.record;
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="num w-10 font-bold">{side.team.abbrev}</span>
          <span className="truncate text-sm text-muted">{side.team.name}</span>
        </div>
        <div className="num pl-12 text-xs text-muted">
          {r ? (
            <>
              {r.w}-{r.l}-{r.otl}
              {r.l10 && ` · L10 ${r.l10}`}
              {r.streak && ` · ${r.streak}`}
            </>
          ) : (
            <Missing reason={side.record_reason} />
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <RestChip side={side} />
        {done && <span className="num w-6 text-right text-lg font-semibold">{side.score ?? "—"}</span>}
      </div>
    </div>
  );
}

function GameCard({ g }: { g: GameSummary }) {
  const done = g.status === "final" || g.status === "live";
  return (
    <Link
      to={`/games/${g.id}`}
      className="block rounded-lg border border-line bg-panel transition-colors hover:border-accent/50"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-2 text-xs text-muted">
        <span className="num font-semibold text-text">{statusLabel(g)}</span>
        <span className="truncate pl-3">
          {g.venue ?? "Venue unavailable"}
          {g.neutral_site && " · neutral site"}
        </span>
      </div>
      <div className="flex flex-col gap-2.5 p-4">
        <TeamRow side={g.away} done={done} />
        <TeamRow side={g.home} done={done} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-xs text-muted">
        <span>
          Goalies:{" "}
          {g.away.goalie || g.home.goalie ? (
            [g.away, g.home].map((s, i) => (
              <span key={s.team.abbrev}>
                {i > 0 && " · "}
                {s.goalie ? (
                  <>
                    {s.goalie.name.split(" ").slice(-1)[0]}
                    <span className="text-[10px]">
                      {" "}
                      {s.goalie.status === "projected"
                        ? `(proj. ${Math.round(s.goalie.probability * 100)}%)`
                        : s.goalie.status === "confirmed"
                          ? "(confirmed)"
                          : ""}
                    </span>
                  </>
                ) : (
                  "?"
                )}
              </span>
            ))
          ) : (
            <Missing reason={g.home.goalie_reason} />
          )}
        </span>
        {g.model?.p_home_win != null && (
          <span className="num">
            Model: {g.home.team.abbrev} {Math.round(g.model.p_home_win * 100)}%
            {g.model.goals_home != null &&
              g.model.goals_away != null &&
              ` · goals ${g.model.goals_away.toFixed(1)}–${g.model.goals_home.toFixed(1)}`}
          </span>
        )}
        <span>
          Odds: <Missing reason={g.environment_reason} />
        </span>
      </div>
    </Link>
  );
}

export function Games() {
  const manifest = useManifest().data!;
  const [params, setParams] = useSearchParams();
  const dates = slateDates(manifest.files);
  const date = params.get("date") ?? manifest.slate_date;
  const slate = useEncrypted<Slate>(`slate/${date}.json`);
  const schedule = manifest.feeds.find((f) => f.code === "schedule");

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Games</h1>
        {schedule && schedule.state !== "ok" && <DataChip state={schedule.state} />}
      </div>

      {dates.length > 0 && (
        <nav className="-mx-1 flex gap-1 overflow-x-auto pb-1" aria-label="Dates">
          {dates.map((d) => (
            <button
              key={d}
              onClick={() => setParams(d === manifest.slate_date ? {} : { date: d })}
              aria-current={d === date ? "date" : undefined}
              className={`min-h-10 shrink-0 rounded-md px-3 text-xs ${
                d === date ? "bg-accent text-bg" : "border border-line text-muted hover:text-text"
              }`}
            >
              {d === manifest.slate_date ? "Today" : longDate(d)}
            </button>
          ))}
        </nav>
      )}

      {slate.state === "loading" && <Spinner label="Loading slate…" />}
      {slate.state === "error" && <Notice tone="bad">{slate.error.message}</Notice>}
      {slate.state === "unavailable" && (
        <Notice tone="warn">
          Live data unavailable for {longDate(date)}.{" "}
          {schedule?.state === "ok" ? "This date is outside the published window." : schedule?.reason}
        </Notice>
      )}
      {slate.state === "ready" && (
        <>
          <p className="text-sm text-muted">
            {longDate(date)} · {slate.value.data.games.length} game{slate.value.data.games.length === 1 ? "" : "s"}
          </p>
          {slate.value.data.games.length === 0 ? (
            <Panel>
              <p className="text-sm text-muted">No NHL games scheduled.</p>
            </Panel>
          ) : (
            <div className="grid gap-3">
              {slate.value.data.games.map((g) => (
                <GameCard key={g.id} g={g} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
