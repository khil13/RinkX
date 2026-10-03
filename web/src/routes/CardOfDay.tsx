import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { odds, pts } from "../components/Lines";
import { pct } from "../components/Projections";
import { ScoreBadge } from "../components/Scores";
import { TeamChip, TeamStripe } from "../components/Team";
import { Notice, Panel, Spinner } from "../components/ui";
import { buildCard } from "../lib/card";
import { useEncrypted, useManifest } from "../lib/data/fetch";
import type { BestProps as BestPropsData, GameSummary, PropRow, Slate } from "../lib/data/types";
import { clock, localTime, longDate } from "../lib/format";
import { betText, Drawer, propTeams } from "./BestProps";

const evText = (ev: number | null) => (ev === null ? "—" : `${ev >= 0 ? "+" : "−"}${Math.abs(ev).toFixed(2)}u`);

function Pick({ r, rank, onOpen }: { r: PropRow; rank: number; onOpen: () => void }) {
  const teams = propTeams(r);
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="relative flex w-full flex-col gap-1.5 overflow-hidden rounded-lg border border-line bg-panel-2 p-3 pl-5 text-left hover:border-accent/50"
        aria-label={`${r.subject.name} ${betText(r)}`}
      >
        <TeamStripe teams={teams} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex flex-wrap items-center gap-2">
            <span className="num text-xs text-muted">#{rank}</span>
            {teams.map((t) => (
              <TeamChip key={t} abbrev={t} />
            ))}
            <span className="text-sm font-semibold">{r.subject.type === "player" ? r.subject.name : betText(r)}</span>
            <ScoreBadge score={r.scores?.intelligence} label="Prop Intelligence" />
          </span>
          <span className="num text-xs text-muted">
            {r.game.away} @ {r.game.home} · {clock(r.game.start_time_utc)}
          </span>
        </div>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-base">
            {r.subject.type === "player" && <>{betText(r)} </>}
            <span className="num font-semibold">{odds(r.price)}</span>{" "}
            <span className="text-xs text-muted">at {r.book_name}</span>
          </span>
          <span className="num text-xs">
            edge {pts(r.edge)} pts · EV {evText(r.ev)} · conf <span className="font-semibold">{r.confidence ?? "—"}</span>
          </span>
        </div>
        <div className="num flex flex-wrap justify-between gap-2 text-[11px] text-muted">
          <span>
            model {pct(r.p_model)} vs {r.market_is_novig ? "no-vig" : "implied"} {r.p_market !== null ? pct(r.p_market) : "—"}
          </span>
          <span>
            via {r.source} · line seen {localTime(r.line_seen_at)}
          </span>
        </div>
      </button>
    </li>
  );
}

function Section({
  title,
  picks,
  total,
  empty,
  onOpen,
}: {
  title: string;
  picks: PropRow[];
  total: number;
  empty: string;
  onOpen: (r: PropRow) => void;
}) {
  return (
    <Panel
      title={title}
      right={total > picks.length ? <span className="text-[11px] text-muted">top {picks.length} of {total}</span> : undefined}
    >
      {picks.length === 0 ? (
        <p className="text-sm text-muted">{empty}</p>
      ) : (
        <ol className="flex flex-col gap-2" aria-label={title}>
          {picks.map((r, i) => (
            <Pick key={r.prediction_id} r={r} rank={i + 1} onOpen={() => onOpen(r)} />
          ))}
        </ol>
      )}
    </Panel>
  );
}

function GamesStrip({ games, counts }: { games: GameSummary[]; counts: Map<number, number> }) {
  if (games.length === 0) return <p className="text-sm text-muted">No NHL games on this date.</p>;
  return (
    <ul className="flex gap-2 overflow-x-auto pb-1" aria-label="Games on this date">
      {games.map((g) => (
        <li key={g.id} className="shrink-0">
          <Link
            to={`/games/${g.id}`}
            className="flex min-h-11 flex-col gap-1 rounded-md border border-line bg-panel-2 px-2.5 py-1.5 hover:border-accent/50"
          >
            <span className="flex items-center gap-1">
              <TeamChip abbrev={g.away.team.abbrev} />
              <span className="text-[10px] text-muted">@</span>
              <TeamChip abbrev={g.home.team.abbrev} />
            </span>
            <span className="num text-[10px] text-muted">
              {clock(g.start_time_utc)}
              {g.status !== "scheduled" && g.status !== "pregame"
                ? " · started"
                : g.line_count === 0
                  ? " · no lines yet"
                  : ` · ${counts.get(g.id) ?? 0} pick${counts.get(g.id) === 1 ? "" : "s"}`}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function CardOfDay() {
  const res = useEncrypted<BestPropsData>("props/best.json");
  const manifest = useManifest().data;
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState<PropRow | null>(null);
  const data = res.state === "ready" ? res.value.data : null;

  // Always the NHL slate date (Eastern time) unless another date is picked: never a silent jump
  // to some other day. Tabs: today plus any later date with priced lines.
  const today = manifest?.slate_date ?? "";
  const dates = useMemo(
    () => Array.from(new Set([today, ...(data?.rows ?? []).map((r) => r.game.date)].filter((d) => d && d >= today))).sort(),
    [data, today],
  );
  const date = params.get("date") && dates.includes(params.get("date")!) ? params.get("date")! : today;
  const slate = useEncrypted<Slate>(`slate/${date}.json`);
  const games = slate.state === "ready" ? slate.value.data.games : null;
  const gameIds = useMemo(() => (games ? new Set(games.map((g) => g.id)) : null), [games]);
  const card = useMemo(() => buildCard(data?.rows ?? [], date, gameIds), [data, date, gameIds]);
  const counts = useMemo(() => {
    const m = new Map<number, number>();
    for (const r of [...card.team.shown, ...card.player.shown]) m.set(r.game.id, (m.get(r.game.id) ?? 0) + 1);
    return m;
  }, [card]);

  if (res.state === "loading") return <Spinner label="Building the card…" />;
  if (res.state === "error") return <Notice tone="bad">{res.error.message}</Notice>;
  if (res.state === "unavailable" || !data) return <Notice tone="warn">Live data unavailable: no props file published yet.</Notice>;

  let reason: string | null = null;
  if (data.reason === "not_connected") reason = "Sportsbook lines aren't connected (add the ODDS_API_KEY secret).";
  else if (data.reason === "no_lines")
    reason = "No lines for upcoming games yet. Props usually post the day before; the free odds plan fetches the soonest games first.";
  else if (data.reason === "not_priced")
    reason = `${data.open_lines} lines are open, but none has a current projection to price against yet.`;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-lg font-semibold">Card of the Day</h1>
          <span className="num text-xs text-muted">updated {localTime(data.generated_at)}</span>
        </div>
        {dates.length > 0 && (
          <div className="flex gap-1.5 overflow-x-auto pb-1" role="tablist" aria-label="Date">
            {dates.map((d) => (
              <button
                key={d}
                type="button"
                role="tab"
                aria-selected={d === date}
                onClick={() => setParams(d === today ? {} : { date: d }, { replace: true })}
                className={`min-h-9 shrink-0 rounded-full border px-3 text-xs ${
                  d === date ? "border-accent bg-accent/15 text-text" : "border-line text-muted hover:text-text"
                }`}
              >
                {d === today ? "Today" : longDate(d)}
              </button>
            ))}
          </div>
        )}
        <p className="text-xs text-muted">
          The strongest props for {date ? longDate(date) : "the day"}: only lines where the model beats the no-vig market
          by 3+ points with positive expected value and good data; the best price across your books; one pick per
          player and per game; ranked by expected value. Statistical estimates, not guarantees. Tap a pick for the full
          calculation. Every priced line is on{" "}
          <Link to="/props" className="text-accent underline">
            Props
          </Link>
          .
        </p>
      </header>

      {games && <GamesStrip games={games} counts={counts} />}

      {reason ? (
        <Notice>{reason}</Notice>
      ) : (
        <>
          <Section
            title="Team props"
            picks={card.team.shown}
            total={card.team.total}
            empty="No moneyline or total clears the bar on this date. That's normal: game lines are the most efficient."
            onOpen={setOpen}
          />
          <Section
            title="Player props"
            picks={card.player.shown}
            total={card.player.total}
            empty="No player prop clears the bar on this date. Most lines are efficient; nothing is forced onto the card."
            onOpen={setOpen}
          />
        </>
      )}
      {open && <Drawer r={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
