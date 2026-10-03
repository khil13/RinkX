import { Link } from "react-router";
import type {
  Factor,
  GameEnvironment,
  GoalieStart,
  MarketProjection,
  ModelReason,
  ProjectedPlayer,
  SideProjections,
  TotalProjection,
} from "../lib/data/types";
import { githubRepo, localTime, mmss } from "../lib/format";

export const MODEL_REASON_TEXT: Record<ModelReason, string> = {
  models_not_ready:
    "No projections yet: the models haven't been tested on enough completed games. They are published only after passing that test.",
  no_model_passed: "No projections: no model has beaten the simple baselines in testing yet.",
  game_started: "Projections are made before the game only.",
  outside_window: "Projections are published for the next three days of games.",
  ruled_out: "Ruled out of his next game (Quick Entry).",
  no_upcoming_projection: "No projection: no game in the next three days, or no NHL history to project from.",
};

const MISSING_TEXT: Record<string, string> = {
  lineup_unconfirmed: "Lineup not confirmed: assumes he dresses",
  goalie_unconfirmed: "Starting goalie not confirmed",
  injuries_not_connected: "No recent injury report",
  injury_day_to_day: "Day-to-day on the injury report",
  odds_not_connected: "No odds yet: game environment from team history only",
};

export function pct(p: number | undefined): string {
  if (p === undefined) return "—";
  if (p > 0 && p < 0.005) return "<1%";
  if (p < 1 && p > 0.995) return ">99%";
  return `${Math.round(p * 100)}%`;
}

function effect(e: number): string {
  const v = Math.round(e * 100);
  return v === 0 ? "0%" : `${v > 0 ? "+" : ""}${v}%`;
}

/** Link that opens a prefilled GitHub issue form; null when not on the published site. */
const QE_TITLE = { goalie: "Quick Entry: goalie", "player-out": "Quick Entry: player out", news: "Quick Entry: news" };
type QuickEntryTemplate = keyof typeof QE_TITLE;

export function quickEntryUrl(template: QuickEntryTemplate, fields: Record<string, string>): string | null {
  const gh = githubRepo();
  if (!gh) return null;
  const q = new URLSearchParams({ template: `quick-entry-${template}.yml`, title: QE_TITLE[template], ...fields });
  return `https://github.com/${gh.owner}/${gh.repo}/issues/new?${q.toString()}`;
}

export function QuickEntryLink({
  template,
  fields,
  children,
}: {
  template: QuickEntryTemplate;
  fields: Record<string, string>;
  children: React.ReactNode;
}) {
  const url = quickEntryUrl(template, fields);
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-h-9 items-center rounded-md border border-line px-2.5 text-xs text-muted hover:text-text"
    >
      {children}
    </a>
  );
}

export function GoalieChip({ g }: { g: GoalieStart }) {
  const label =
    g.status === "confirmed"
      ? "CONFIRMED"
      : g.status === "actual"
        ? "STARTED"
        : g.status === "projected"
          ? `PROJECTED · ${pct(g.probability)}`
          : "UNKNOWN";
  const style = g.status === "confirmed" || g.status === "actual" ? "border-over/40 text-over" : "border-warn/40 text-warn";
  return <span className={`num rounded border px-1.5 py-0.5 text-[10px] font-semibold ${style}`}>{label}</span>;
}

export function GoalieLine({ g, gameId, team }: { g: GoalieStart | null; gameId?: number; team?: string }) {
  if (!g) return <span className="text-xs italic text-muted">Not known yet</span>;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Link to={`/players/${g.id}`} className="hover:text-accent">
        {g.name}
      </Link>
      <GoalieChip g={g} />
      {g.source && (
        <a href={g.source} target="_blank" rel="noreferrer" className="text-xs text-muted underline">
          source
        </a>
      )}
      {g.status === "projected" && gameId !== undefined && team && (
        <QuickEntryLink template="goalie" fields={{ game: String(gameId), team }}>
          Confirm goalie
        </QuickEntryLink>
      )}
    </div>
  );
}

function Bars({ m }: { m: MarketProjection }) {
  const pmf = m.pmf ?? [];
  const shown = pmf.map((p, k) => [k, p] as const).filter(([, p]) => p >= 0.005);
  if (!shown.length) return null;
  const max = Math.max(...shown.map(([, p]) => p));
  return (
    <div className="flex h-16 items-end gap-0.5" aria-label={`${m.label} distribution`} role="img">
      {shown.map(([k, p]) => (
        <div key={k} className="flex min-w-0 flex-1 flex-col items-center justify-end" title={`${k}: ${pct(p)}`}>
          <div className="w-full rounded-sm bg-accent/70" style={{ height: `${Math.max(2, (p / max) * 48)}px` }} />
          <span className="num text-[9px] text-muted">{k}</span>
        </div>
      ))}
    </div>
  );
}

/** "k+" probabilities around the bulk of the distribution. */
function Thresholds({ m }: { m: MarketProjection }) {
  const ks = m.p_ge
    .map((p, k) => [k, p] as const)
    .filter(([k, p]) => k >= 1 && p >= 0.02 && p <= 0.98)
    .slice(0, 8);
  if (!ks.length) return null;
  return (
    <ul className="num flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {ks.map(([k, p]) => (
        <li key={k}>
          <span className="text-muted">{k}+</span> {pct(p)}
        </li>
      ))}
    </ul>
  );
}

function FactorRow({ f }: { f: Factor }) {
  const flat = Math.round(f.effect * 100) === 0;
  const up = f.effect >= 0;
  return (
    <li className="border-t border-line py-1.5 first:border-0">
      <div className="flex items-baseline justify-between gap-2">
        <span>{f.name}</span>
        <span className={`num text-xs font-semibold ${flat ? "text-muted" : up ? "text-over" : "text-bad"}`}>
          {flat ? "·" : up ? "▲" : "▼"} {effect(f.effect)}
        </span>
      </div>
      <p className="text-xs text-muted">{f.detail}</p>
    </li>
  );
}

const INPUT_LABELS: Record<string, string> = {
  games_in_history: "Games in his history",
  starts_in_history: "Starts in his history",
  half_life_games: "Recency half-life (games)",
  prior_strength: "Pull toward league average",
  distribution: "Distribution",
  reference_mean: "Average-player reference",
  expected_toi_s: "Expected ice time",
  expected_pp_toi_s: "Expected PP time",
  expected_shots_against: "Expected shots against",
  expected_save_pct: "Expected save %",
  league_save_pct: "League save %",
  start_probability: "Start probability",
  start_status: "Start status",
  team_scores_first: "Team scores first",
  share_of_team_goals: "His share of team goals",
  expected_team_goals: "Team expected goals",
  expected_goals_for: "Team expected goals",
  expected_goals_against: "Opponent expected goals",
  tied_after_regulation: "Goes past regulation",
};

const UNIT: Record<string, string> = { hours: "h of ice time", "PP hours": "h of PP time", shots: "shots" };

function Explain({ m }: { m: MarketProjection }) {
  const factors = [...(m.factors_for ?? []), ...(m.factors_against ?? [])].sort(
    (a, b) => Math.abs(b.effect) - Math.abs(a.effect),
  );
  const inputs = m.inputs ?? {};
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-xs text-accent">Explain projection</summary>
      <div className="mt-2 flex flex-col gap-2">
        <p className="text-xs text-muted">
          Start from an average player (
          {typeof inputs.reference_mean === "number" ? inputs.reference_mean.toFixed(2) : "—"}), then apply each
          factor. The
          factors multiply to the projected average of {m.mean.toFixed(2)}.
        </p>
        <ul>
          {factors.map((f) => (
            <FactorRow key={f.name} f={f} />
          ))}
        </ul>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          {Object.entries(inputs)
            .filter(([k]) => k in INPUT_LABELS && k !== "reference_mean")
            .map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{INPUT_LABELS[k]}</dt>
                <dd className="num text-right">
                  {k.endsWith("toi_s")
                    ? mmss(v as number)
                    : ["start_probability", "team_scores_first", "share_of_team_goals", "tied_after_regulation"].includes(k)
                      ? pct(v as number)
                      : k === "prior_strength"
                        ? `${String(v)} ${UNIT[String(inputs.prior_strength_unit)] ?? ""}`
                        : String(v)}
                </dd>
              </div>
            ))}
        </dl>
      </div>
    </details>
  );
}

export function ProjectionCard({ m }: { m: MarketProjection }) {
  const yes = m.kind === "yes_no";
  return (
    <article className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3" aria-label={`${m.label} projection`}>
      <header className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{m.label}</h3>
        <span className="num text-lg">{yes ? pct(m.p_ge[1]) : m.mean.toFixed(2)}</span>
      </header>
      <p className="num text-xs text-muted">
        {yes
          ? `Probability of at least one · average ${m.mean.toFixed(2)}`
          : `Projected average · median ${m.median ?? "—"} · std dev ${m.sd ?? "—"}`}
      </p>
      {m.previous && (
        <p className="num rounded bg-accent/10 px-2 py-1 text-xs text-accent">
          Updated after {m.previous.reason}: {m.previous.mean.toFixed(2)} → {m.mean.toFixed(2)}
        </p>
      )}
      {!yes && <Bars m={m} />}
      {!yes && <Thresholds m={m} />}
      <Explain m={m} />
      <p className="text-[11px] text-muted">
        Data quality {Math.round(m.data_quality * 100)}%
        {m.missing_inputs.length > 0 && ` · ${m.missing_inputs.map((x) => MISSING_TEXT[x] ?? x).join(" · ")}`}
      </p>
    </article>
  );
}

const GAME_COLUMNS: [string, string, "mean" | "p1"][] = [
  ["skater_shots_on_goal", "SOG", "mean"],
  ["skater_points", "1+ Pt", "p1"],
  ["skater_anytime_goal", "Goal", "p1"],
  ["skater_first_goal", "1st G", "p1"],
  ["skater_assists", "1+ A", "p1"],
  ["skater_pp_points", "1+ PPP", "p1"],
  ["skater_hits", "Hits", "mean"],
  ["skater_blocked_shots", "Blk", "mean"],
];

function cell(p: ProjectedPlayer, market: string, how: "mean" | "p1"): string {
  const m = p.markets[market];
  if (!m) return "—";
  return how === "mean" ? m.mean.toFixed(1) : pct(m.p_ge[1]);
}

export function SideProjectionTable({ side, abbrev }: { side: SideProjections; abbrev: string }) {
  const skaters = side.players.filter((p) => p.position !== "G");
  const goalies = side.players.filter((p) => p.position === "G");
  const cols = GAME_COLUMNS.filter(([m]) => skaters.some((p) => m in p.markets));
  return (
    <div className="overflow-x-auto">
      <div className="mb-1 text-xs font-semibold text-muted">{abbrev}</div>
      <table className="num w-full min-w-[520px] text-sm" aria-label={`${abbrev} projections`}>
        <thead className="text-xs text-muted">
          <tr>
            <th className="py-1 text-left font-normal">Skater</th>
            <th className="py-1 text-right font-normal">TOI</th>
            {cols.map(([m, label]) => (
              <th key={m} className="py-1 text-right font-normal">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {skaters.map((p) => (
            <tr key={p.id} className="border-t border-line">
              <td className="py-1 pr-2 font-sans">
                <Link className="hover:text-accent" to={`/players/${p.id}`}>
                  {p.name}
                </Link>{" "}
                <span className="text-xs text-muted">{p.position}</span>
              </td>
              <td className="text-right text-muted">{mmss(p.toi_s)}</td>
              {cols.map(([m, , how]) => (
                <td key={m} className="text-right">
                  {cell(p, m, how)}
                </td>
              ))}
            </tr>
          ))}
          {goalies.map((g) => (
            <tr key={g.id} className="border-t border-line text-muted">
              <td className="py-1 pr-2 font-sans text-text">
                <Link className="hover:text-accent" to={`/players/${g.id}`}>
                  {g.name}
                </Link>{" "}
                <span className="text-xs">G</span>
              </td>
              <td colSpan={cols.length + 1} className="text-right">
                {g.markets.goalie_saves ? `${g.markets.goalie_saves.mean.toFixed(1)} saves` : ""}
                {g.markets.goalie_goals_against ? ` · ${g.markets.goalie_goals_against.mean.toFixed(2)} GA` : ""}
                {g.markets.goalie_win ? ` · win ${pct(g.markets.goalie_win.p_ge[1])}` : ""}
                {g.markets.goalie_shutout ? ` · shutout ${pct(g.markets.goalie_shutout.p_ge[1])}` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {side.out.length > 0 && (
        <p className="mt-1 text-xs text-muted">
          Out:{" "}
          {side.out.map((o, i) => (
            <span key={o.id}>
              {i > 0 && ", "}
              {o.name}
              {o.reason ? ` (${o.reason})` : ""}{" "}
              <a href={o.source} target="_blank" rel="noreferrer" className="underline">
                source
              </a>
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

export function TestedNote({ testedAt }: { testedAt: string | null }) {
  return (
    <p className="text-xs text-muted">
      Projected averages; percentages are the model's probability of reaching that number. Only stats whose
      model beat simple baselines on past games are shown (
      <Link to="/models" className="underline">
        model tests
      </Link>
      {testedAt ? `, last run ${localTime(testedAt)}` : ""}). Statistical estimates, not guarantees.
    </p>
  );
}

function TotalLine({ label, t }: { label: string; t: TotalProjection | undefined }) {
  if (!t) return null;
  const ks = t.p_ge
    .map((p, k) => [k, p] as const)
    .filter(([k, p]) => k >= 1 && p >= 0.05 && p <= 0.95)
    .slice(0, 6);
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-t border-line py-1.5 text-sm">
      <span>
        {label} <span className="num text-muted">avg {t.mean.toFixed(2)}</span>
      </span>
      <span className="num flex flex-wrap gap-x-2 text-xs">
        {ks.map(([k, p]) => (
          <span key={k}>
            <span className="text-muted">{k}+</span> {pct(p)}
          </span>
        ))}
      </span>
    </div>
  );
}

/** Model-only game outlook: win probability and goal totals. No odds are involved. */
export function GameOutlook({ env, away, home }: { env: GameEnvironment; away: string; home: string }) {
  const w = env.win;
  return (
    <div className="flex flex-col gap-2" aria-label="Game outlook">
      {w && (
        <div>
          <div className="num flex justify-between text-sm">
            <span>
              {away} {pct(w.away)}
            </span>
            <span>
              {home} {pct(w.home)}
            </span>
          </div>
          <div className="mt-1 flex h-2 overflow-hidden rounded bg-panel-2" role="img" aria-label={`${home} win probability ${pct(w.home)}`}>
            <div className="bg-muted/50" style={{ width: `${w.away * 100}%` }} />
            <div className="bg-accent" style={{ width: `${w.home * 100}%` }} />
          </div>
          <p className="num mt-1 text-xs text-muted">
            Win probability incl. overtime and shootout · goes past regulation {pct(w.tied_after_regulation ?? undefined)}
          </p>
          {w.previous && (
            <p className="num mt-1 rounded bg-accent/10 px-2 py-1 text-xs text-accent">
              Updated after {w.previous.reason}: {home} {pct(w.previous.home)} → {pct(w.home)}
            </p>
          )}
        </div>
      )}
      <div>
        <TotalLine label={`${away} goals`} t={env.totals.away} />
        <TotalLine label={`${home} goals`} t={env.totals.home} />
        <TotalLine label="Total goals" t={env.totals.game} />
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-xs text-accent">Explain outlook</summary>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          {(
            [
              [away, env.totals.away],
              [home, env.totals.home],
            ] as const
          ).map(
            ([abbrev, t]) =>
              t && (
                <div key={abbrev}>
                  <p className="text-xs text-muted">
                    {abbrev}: league-average {t.reference_mean?.toFixed(2)} goals × these factors = {t.mean.toFixed(2)}{" "}
                    (before overtime goals)
                  </p>
                  <ul>
                    {t.factors.map((f) => (
                      <FactorRow key={f.name} f={f} />
                    ))}
                  </ul>
                </div>
              ),
          )}
        </div>
      </details>
      <p className="text-xs text-muted">
        Model only: no odds are connected. Shootout "goals" are not counted as goals.
      </p>
    </div>
  );
}
