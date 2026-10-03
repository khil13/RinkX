import { ConfidenceVsValue, PublicBetting, ScoreBadge, ScoreDetails, WhyThisProp } from "../components/Scores";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { odds, ConfidenceBreakdown, pts, SIDE_LABEL } from "../components/Lines";
import { pct } from "../components/Projections";
import { TeamChip, TeamStripe } from "../components/Team";
import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import { legFromRow, parlayStore, useParlayLegs } from "../lib/parlayStore";
import { PlaceBet } from "../components/PlaceBet";
import type { BestProps as BestPropsData, PropRow } from "../lib/data/types";
import { clock, localTime, longDate } from "../lib/format";
import { ADVANCED, type Advanced, matches, SORT_LABELS, SORTS, type SortKey } from "../lib/propFilters";
import { Freshness } from "../components/Freshness";


interface Filters {
  date: string;
  game: string;
  market: string;
  book: string;
  side: string;
  minEdge: number;
  minConf: number;
  sort: SortKey;
  adv: Advanced;
}

const DEFAULTS: Filters = {
  date: "",
  game: "",
  market: "",
  book: "",
  side: "",
  minEdge: 0,
  minConf: 0,
  sort: "ev",
  adv: ADVANCED,
};
const STORE_FILTERS = "rinkx.props.filters";
const STORE_SEEN = "rinkx.props.seen"; // line key -> prediction id last seen on this device

function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? ({ ...fallback, ...JSON.parse(raw) } as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode or storage blocked: filters just don't persist */
  }
}

const lineKey = (r: PropRow) => `${r.game.id}|${r.subject.name}|${r.market}|${r.book}`;

/** The team(s) a prop is about: the player's team, the team picked on a moneyline, or both teams. */
export function propTeams(r: PropRow): string[] {
  if (r.subject.type === "player") return r.subject.team ? [r.subject.team] : [];
  if (r.kind === "moneyline") {
    const side = r.lean ?? r.side_scored;
    return [side === "home" ? r.game.home : r.game.away];
  }
  return [r.game.away, r.game.home];
}

export function betText(r: PropRow) {
  const side = SIDE_LABEL[r.lean ?? r.side_scored] ?? r.side_scored;
  if (r.kind === "moneyline") return `${side === "Home" ? r.game.home : r.game.away} to win`;
  return `${side}${r.line !== null ? ` ${r.line}` : ""} ${r.market_label}`;
}

function Chip({ kind }: { kind: "new" | "moved" }) {
  return (
    <span
      className={`rounded border px-1 text-[10px] font-semibold ${
        kind === "new" ? "border-accent/50 text-accent" : "border-warn/50 text-warn"
      }`}
    >
      {kind === "new" ? "NEW" : "PRICE MOVED"}
    </span>
  );
}

function PropCard({ r, change, onOpen }: { r: PropRow; change: "new" | "moved" | null; onOpen: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="relative flex w-full flex-col gap-1 overflow-hidden rounded-md border border-line bg-panel-2 p-3 pl-4 text-left hover:border-accent/50"
        aria-label={`${r.subject.name} ${betText(r)}`}
      >
        <TeamStripe teams={propTeams(r)} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">{r.subject.name}</span>
            {r.subject.team && <TeamChip abbrev={r.subject.team} />}
            {change && <Chip kind={change} />}
            <ScoreBadge score={r.scores?.intelligence} label="Prop Intelligence" />
          </span>
          <span className="num text-xs text-muted">
            {r.game.away} @ {r.game.home} · {clock(r.game.start_time_utc)}
          </span>
        </div>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-sm">
            {r.lean ? betText(r) : <span className="text-muted">No lean · {betText(r)}</span>}{" "}
            <span className="num font-semibold">{odds(r.price)}</span>
            {r.projection != null && r.line !== null && (
              <span className="num ml-1 text-xs text-muted">
                proj {r.projection.toFixed(2)} ({r.projection - r.line >= 0 ? "+" : "−"}
                {Math.abs(r.projection - r.line).toFixed(2)})
              </span>
            )}
          </span>
          <span className="num text-xs">
            edge {pts(r.edge)} pts · EV {r.ev !== null ? `${r.ev >= 0 ? "+" : "−"}${Math.abs(r.ev).toFixed(2)}u` : "—"} ·
            conf <span className="font-semibold">{r.confidence ?? "—"}</span>
          </span>
        </div>
        <div className="num flex flex-wrap justify-between gap-2 text-[11px] text-muted">
          <span>
            model {pct(r.p_model)} vs {r.market_is_novig ? "no-vig" : "implied"} {r.p_market !== null ? pct(r.p_market) : "—"}
            {r.scores?.shot_environment != null && ` · Shot Environment ${r.scores.shot_environment}/100`}
          </span>
          <span>
            {r.book_name} via {r.source} · line seen {localTime(r.line_seen_at)}
          </span>
        </div>
      </button>
      {r.price !== null && (
        <div className="mt-1 flex justify-end">
          <PlaceBet r={r} compact />
        </div>
      )}
    </li>
  );
}

function ParlayButton({ r }: { r: PropRow }) {
  const legs = useParlayLegs();
  const leg = legFromRow(r);
  if (!leg) return null;
  const inParlay = legs.some((l) => l.key === leg.key);
  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={() => (inParlay ? parlayStore.remove(leg.key) : parlayStore.add(leg))}
        className="min-h-9 rounded-md border border-line px-3 text-sm hover:border-accent/50"
      >
        {inParlay ? "Remove from parlay" : "Add to parlay"}
      </button>
      {legs.length > 0 && (
        <Link to="/parlay" className="text-xs text-accent underline">
          Parlay ({legs.length})
        </Link>
      )}
    </div>
  );
}

export function Drawer({ r, onClose }: { r: PropRow; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  const parts = r.confidence_parts;
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="m-auto w-[min(36rem,calc(100vw-2rem))] rounded-lg border border-line bg-panel p-0 text-text backdrop:bg-black/60 max-sm:sheet-in max-sm:mb-0 max-sm:w-full max-sm:max-w-full max-sm:rounded-b-none max-sm:pb-[env(safe-area-inset-bottom)]"
      aria-label="Prop card"
    >
      <div className="flex max-h-[85vh] flex-col gap-3 overflow-y-auto p-4">
        <header className="flex items-start justify-between gap-3">
          <div>
            <div className="mb-1 flex gap-1">
              {propTeams(r).map((t) => (
                <TeamChip key={t} abbrev={t} />
              ))}
            </div>
            <h2 className="text-base font-semibold">
              {r.subject.name} · {betText(r)}
            </h2>
            <p className="num text-xs text-muted">
              {r.game.away} @ {r.game.home} · {longDate(r.game.date)} {clock(r.game.start_time_utc)}
            </p>
          </div>
          <button type="button" onClick={() => ref.current?.close()} className="min-h-9 rounded px-2 text-sm text-muted hover:text-text">
            Close
          </button>
        </header>
        <dl className="num grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted">Price</dt>
          <dd>
            {odds(r.price)} at {r.book_name}
          </dd>
          <dt className="text-muted">Model</dt>
          <dd>{pct(r.p_model)}</dd>
          <dt className="text-muted">Market ({r.market_is_novig ? "no-vig" : "implied, margin included"})</dt>
          <dd>{r.p_market !== null ? pct(r.p_market) : "—"}</dd>
          <dt className="text-muted">Edge</dt>
          <dd>{pts(r.edge)} pts</dd>
          <dt className="text-muted">Expected value</dt>
          <dd>{r.ev !== null ? `${r.ev >= 0 ? "+" : "−"}${Math.abs(r.ev).toFixed(3)} units per unit staked` : "—"}</dd>
          <dt className="text-muted">Lean</dt>
          <dd>{r.lean ? SIDE_LABEL[r.lean] : "None at this price"}</dd>
          {r.projection != null && r.line !== null && (
            <>
              <dt className="text-muted">Projection vs line</dt>
              <dd>
                {r.projection.toFixed(2)} vs {r.line} ({r.projection - r.line >= 0 ? "+" : "−"}
                {Math.abs(r.projection - r.line).toFixed(2)})
              </dd>
            </>
          )}
        </dl>
        <WhyThisProp r={r} />
        <ScoreDetails r={r} />
        <ConfidenceVsValue r={r} />
        <PublicBetting />
        {parts && (
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">
              Confidence {parts.score}/100
            </h3>
            <ConfidenceBreakdown p={parts} />
          </section>
        )}
        <section>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Calculation</h3>
          <ol className="num list-decimal pl-5 text-[11px] text-muted">
            {r.calculation.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ol>
        </section>
        <p className="text-[11px] text-muted">
          Source: {r.book_name} via {r.source}. Line seen {localTime(r.line_seen_at)}, last changed{" "}
          {localTime(r.line_changed_at)}; priced {localTime(r.priced_at)}. Prediction #{r.prediction_id} is frozen
          and will be graded after the game. Statistical estimates, not guarantees.
        </p>
        <ParlayButton r={r} />
        <PlaceBet r={r} />
        {r.subject.type === "player" && (
          <Link to={`/players/${r.subject.id}`} className="text-sm text-accent underline">
            Player page: projection, Explain, hit rates
          </Link>
        )}
      </div>
    </dialog>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  options: [string | number, string][];
}) {
  return (
    <label className="flex flex-col gap-0.5 text-[11px] text-muted">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="min-h-9 rounded border border-line bg-panel px-2 text-sm text-text"
      >
        {options.map(([v, l]) => (
          <option key={String(v)} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}


function AdvancedFilters({ a, teams, onChange }: { a: Advanced; teams: string[]; onChange: (a: Advanced) => void }) {
  const set = (patch: Partial<Advanced>) => onChange({ ...a, ...patch });
  const active = JSON.stringify(a) !== JSON.stringify(ADVANCED);
  const input = "min-h-9 rounded border border-line bg-panel px-2 text-sm text-text";
  return (
    <details className="mt-2" open={active}>
      <summary className="cursor-pointer text-xs text-accent">More filters{active ? " (on)" : ""}</summary>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4" role="group" aria-label="More filters">
        <label className="flex flex-col gap-0.5 text-[11px] text-muted">
          Player
          <input className={input} value={a.player} onChange={(e) => set({ player: e.target.value })} placeholder="Name" />
        </label>
        <Select label="Team" value={a.team} onChange={(v) => set({ team: v })} options={[["", "Any team"], ...teams.map((t) => [t, t] as [string, string])]} />
        <label className="flex flex-col gap-0.5 text-[11px] text-muted">
          Line
          <input className={input} inputMode="decimal" value={a.line} onChange={(e) => set({ line: e.target.value })} placeholder="e.g. 2.5" />
        </label>
        <Select label="Home/away" value={a.venue} onChange={(v) => set({ venue: v as Advanced["venue"] })}
          options={[["", "Either"], ["home", "Home"], ["away", "Away"]]} />
        <label className="flex flex-col gap-0.5 text-[11px] text-muted">
          Odds from
          <input className={input} inputMode="numeric" value={a.minOdds} onChange={(e) => set({ minOdds: e.target.value })} placeholder="-200" />
        </label>
        <label className="flex flex-col gap-0.5 text-[11px] text-muted">
          Odds to
          <input className={input} inputMode="numeric" value={a.maxOdds} onChange={(e) => set({ maxOdds: e.target.value })} placeholder="+300" />
        </label>
        <Select label="Min model probability" value={a.minProb} onChange={(v) => set({ minProb: Number(v) })}
          options={[[0, "Any"], [50, "50%+"], [55, "55%+"], [60, "60%+"], [65, "65%+"]]} />
        <Select label="Min Prop Intelligence" value={a.minScore} onChange={(v) => set({ minScore: Number(v) })}
          options={[[0, "Any"], [60, "60+"], [70, "70+"], [80, "80+"], [90, "90+"]]} />
        <Select label="Min last-10 hit rate" value={a.minHit} onChange={(v) => set({ minHit: Number(v) })}
          options={[[0, "Any"], [50, "50%+"], [60, "60%+"], [70, "70%+"], [80, "80%+"]]} />
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={a.pp1} onChange={(e) => set({ pp1: e.target.checked })} /> PP1 only
        </label>
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={a.topLine} onChange={(e) => set({ topLine: e.target.checked })} /> Top line / pair only
        </label>
      </div>
    </details>
  );
}

export function BestProps({ all = false }: { all?: boolean }) {
  const res = useEncrypted<BestPropsData>("props/best.json");
  const [f, setF] = useState<Filters>(() => {
    const v = load(STORE_FILTERS, DEFAULTS);
    return { ...v, adv: { ...ADVANCED, ...(v.adv ?? {}) }, sort: v.sort in SORTS ? v.sort : "ev" };
  });
  const [open, setOpen] = useState<PropRow | null>(null);
  const [sheet, setSheet] = useState(false);
  const data = res.state === "ready" ? res.value.data : null;
  useEffect(() => {
    if (!sheet) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSheet(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheet]);

  // Change-aware: compare with the prediction ids this device saw last time, then remember these.
  const [seen] = useState<Record<string, number> | null>(() => {
    const s = load<Record<string, number> | null>(STORE_SEEN, null);
    return s && Object.keys(s).length ? s : null;
  });
  useEffect(() => {
    if (data) save(STORE_SEEN, Object.fromEntries(data.rows.map((r) => [lineKey(r), r.prediction_id])));
  }, [data]);
  const change = (r: PropRow): "new" | "moved" | null => {
    if (!seen) return null; // first visit: nothing to compare with
    const prev = seen[lineKey(r)];
    if (prev === undefined) return "new";
    return prev !== r.prediction_id ? "moved" : null;
  };

  const update = (patch: Partial<Filters>) => {
    const next = { ...f, ...patch };
    setF(next);
    save(STORE_FILTERS, next);
  };

  const pool = useMemo(() => (data ? data.rows.filter((r) => all || r.lean) : []), [data, all]);
  const rows = useMemo(
    () =>
      pool
        .filter(
          (r) =>
            (!f.date || r.game.date === f.date) &&
            (!f.game || String(r.game.id) === f.game) &&
            (!f.market || r.market === f.market) &&
            (!f.book || r.book === f.book) &&
            (!f.side || (r.lean ?? r.side_scored) === f.side) &&
            (r.edge ?? -1) * 100 >= f.minEdge &&
            (r.confidence ?? 0) >= f.minConf &&
            matches(r, f.adv),
        )
        .sort(SORTS[f.sort]),
    [pool, f],
  );

  if (res.state === "loading") return <Spinner label="Loading props…" />;
  if (res.state === "error") return <Notice tone="bad">{res.error.message}</Notice>;
  if (res.state === "unavailable" || !data) return <Notice tone="warn">Live data unavailable: no props file published yet.</Notice>;

  const uniq = <T,>(xs: T[]) => Array.from(new Set(xs));
  const nOn =
    (["date", "game", "market", "book", "side", "minEdge", "minConf"] as const).filter((k) => f[k] !== DEFAULTS[k]).length +
    (JSON.stringify(f.adv) !== JSON.stringify(DEFAULTS.adv) ? 1 : 0);
  const dates = uniq(data.rows.map((r) => r.game.date)).sort();
  const games = uniq(data.rows.map((r) => `${r.game.id}|${r.game.away} @ ${r.game.home}`));
  const markets = uniq(data.rows.map((r) => `${r.market}|${r.market_label}`));
  const books = uniq(data.rows.map((r) => `${r.book}|${r.book_name}`));
  const leans = data.rows.filter((r) => r.lean).length;

  let empty: string | null = null;
  if (data.reason === "not_connected") empty = "Sportsbook lines aren't connected (add the ODDS_API_KEY secret).";
  else if (data.reason === "no_lines")
    empty = "No lines for upcoming games yet. Props usually post the day before; the free odds plan fetches the soonest games first.";
  else if (data.reason === "not_priced")
    empty = `${data.open_lines} lines are open, but none has a current projection to price against yet (the models price only stats that passed their test).`;
  else if (!all && leans === 0)
    empty = `${data.rows.length} lines are priced and none clears the bar (edge ≥ 3 pts with positive EV and good data). That's normal: most lines are efficient. See all priced lines under Props.`;
  else if (rows.length === 0) empty = "Your filters hide every prop.";

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold">{all ? "Props" : "Best Props"}</h1>
        <span className="num text-xs text-muted">
          {all ? `${data.rows.length} priced lines` : `${leans} leans of ${data.rows.length} priced lines`} · updated{" "}
          {localTime(data.generated_at)}
        </span>
      </header>
      <Freshness at={res.value.meta.generated_at} staleAfterMin={120} source="RinkX pipeline (lines via The Odds API)" />
      <p className="text-xs text-muted">
        {all
          ? "Every sportsbook line RinkX has priced for upcoming games, including ones with no lean."
          : "Lines where the model's probability beats the no-vig market by at least 3 points with positive expected value and good data."}{" "}
        Statistical estimates, not guarantees. Tap a prop for the full calculation.
      </p>

      {/* Phone: the filters live in a bottom sheet behind one button, so the props come first. */}
      <div className="flex items-center justify-between gap-2 sm:hidden">
        <button
          type="button"
          onClick={() => setSheet(true)}
          aria-expanded={sheet}
          className="min-h-11 rounded-md border border-line bg-panel px-4 text-sm"
        >
          Filters{nOn ? ` · ${nOn} on` : ""}
        </button>
        <span className="num text-xs text-muted">
          {rows.length} shown · {SORT_LABELS[f.sort]}
        </span>
      </div>
      {sheet && (
        <button type="button" aria-label="Close filters" className="fixed inset-0 z-40 bg-black/50 sm:hidden" onClick={() => setSheet(false)} />
      )}
      <div
        className={
          sheet
            ? "max-sm:sheet-in max-sm:fixed max-sm:inset-x-0 max-sm:bottom-0 max-sm:z-50 max-sm:max-h-[85vh] max-sm:overflow-y-auto max-sm:rounded-t-2xl max-sm:pb-[env(safe-area-inset-bottom)]"
            : "max-sm:hidden"
        }
      >
      <Panel>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="group" aria-label="Filters">
          <Select label="Date" value={f.date} onChange={(v) => update({ date: v })}
            options={[["", "All dates"], ...dates.map((d) => [d, longDate(d)] as [string, string])]} />
          <Select label="Game" value={f.game} onChange={(v) => update({ game: v })}
            options={[["", "All games"], ...games.map((g) => g.split("|") as [string, string])]} />
          <Select label="Market" value={f.market} onChange={(v) => update({ market: v })}
            options={[["", "All markets"], ...markets.map((m) => m.split("|") as [string, string])]} />
          <Select label="Book" value={f.book} onChange={(v) => update({ book: v })}
            options={[["", "All books"], ...books.map((b) => b.split("|") as [string, string])]} />
          <Select label="Side" value={f.side} onChange={(v) => update({ side: v })}
            options={[["", "Any side"], ["over", "Over"], ["under", "Under"], ["yes", "Yes"], ["home", "Home"], ["away", "Away"]]} />
          <Select label="Min edge" value={f.minEdge} onChange={(v) => update({ minEdge: Number(v) })}
            options={[[0, "Any"], [3, "3+ pts"], [5, "5+ pts"], [8, "8+ pts"]]} />
          <Select label="Min confidence" value={f.minConf} onChange={(v) => update({ minConf: Number(v) })}
            options={[[0, "Any"], [50, "50+"], [60, "60+"], [70, "70+"]]} />
          <Select label="Sort by" value={f.sort} onChange={(v) => update({ sort: v as SortKey })}
            options={Object.entries(SORT_LABELS) as [string, string][]} />
        </div>
        <AdvancedFilters a={f.adv} teams={uniq(data.rows.flatMap((r) => [r.game.home, r.game.away])).sort()}
          onChange={(adv) => update({ adv })} />
        {JSON.stringify(f) !== JSON.stringify(DEFAULTS) && (
          <button type="button" onClick={() => update(DEFAULTS)} className="mt-2 min-h-9 text-xs text-accent underline">
            Reset filters
          </button>
        )}
        <button
          type="button"
          onClick={() => setSheet(false)}
          className="mt-3 min-h-11 w-full rounded-md bg-accent text-sm font-semibold text-bg sm:hidden"
        >
          Show {rows.length} prop{rows.length === 1 ? "" : "s"}
        </button>
      </Panel>
      </div>

      {empty ? (
        <Notice>{empty}</Notice>
      ) : (
        <ul className="flex flex-col gap-2" aria-label="Props">
          {rows.map((r) => (
            <PropCard key={r.prediction_id} r={r} change={change(r)} onOpen={() => setOpen(r)} />
          ))}
        </ul>
      )}
      {open && <Drawer r={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
