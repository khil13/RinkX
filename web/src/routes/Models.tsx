import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { ModelsReport, StatTest } from "../lib/data/types";
import { longDate, localTime } from "../lib/format";

const BASELINE_LABEL: Record<string, string> = {
  season: "season average",
  l10: "last-10 average",
  home_rate: "home-team win rate",
  log5_record: "standings records (log5)",
  league_rate: "league shutout rate",
  goalie_season_rate: "goalie's season shutout rate",
  equal_chance: "equal chance per skater",
  season_goal_share: "season goal share",
};

const REASON: Record<string, string> = {
  did_not_beat_baselines: "Did not clearly beat the simple baselines",
  miscalibrated: "Probabilities were not well calibrated",
  insufficient_history: "Not enough completed games to test yet",
};

const FACTOR_NAMES: Record<string, string> = {
  opp: "opponent",
  opp_shots: "opponent shots allowed",
  goalie: "opposing goalie",
  home: "home/road",
  venue: "arena scorekeeping",
  fd: "own defence",
  fo: "opponent offence",
  fin: "team finishing",
};

function Pit({ hist }: { hist: number[] }) {
  const max = Math.max(...hist, 0.2);
  return (
    <div className="flex h-10 items-end gap-0.5" role="img" aria-label="Calibration histogram">
      {hist.map((h, i) => (
        <div
          key={i}
          className="flex-1 rounded-sm bg-accent/60"
          style={{ height: `${Math.max(2, (h / max) * 40)}px` }}
          title={`${i * 10}–${i * 10 + 10}%: ${(h * 100).toFixed(1)}%`}
        />
      ))}
    </div>
  );
}

function Diff({ label, b }: { label: string; b: NonNullable<StatTest["baselines"]>[string] }) {
  const d = b.model_minus_baseline;
  const better = d.lo > 0;
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="text-muted">vs {label}</span>
      <span className={`num ${better ? "text-over" : "text-bad"}`}>
        {d.mean >= 0 ? "+" : ""}
        {d.mean.toFixed(4)} <span className="text-muted">(95% low {d.lo.toFixed(4)})</span>
      </span>
    </div>
  );
}

function StatCard({ stat, t }: { stat: string; t: StatTest }) {
  return (
    <article className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3" aria-label={`${t.label} test`}>
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t.label}</h3>
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
            t.passed ? "border-over/40 text-over" : "border-line text-muted"
          }`}
        >
          {t.passed ? "PASSED · PUBLISHED" : "NOT PUBLISHED"}
        </span>
      </header>
      {!t.passed && t.reason && <p className="text-xs text-muted">{REASON[t.reason]}</p>}
      <p className="num text-xs text-muted">
        {t.n_test.toLocaleString()} test games · {t.n_tune.toLocaleString()} tuning games
      </p>
      {t.baselines && (
        <div className="flex flex-col gap-0.5">
          {Object.entries(t.baselines).map(([k, b]) => (
            <Diff key={k} label={BASELINE_LABEL[k] ?? k} b={b} />
          ))}
        </div>
      )}
      {t.mean_pred !== undefined && (
        <p className="num text-xs">
          <span className="text-muted">Average projected</span> {t.mean_pred.toFixed(t.mean_pred < 1 ? 3 : 2)}{" "}
          <span className="text-muted">· actual</span> {t.mean_actual?.toFixed(t.mean_pred < 1 ? 3 : 2)}
        </p>
      )}
      {t.calibration_p !== undefined && (
        <p className="text-[11px] text-muted">
          Calibration test p = {t.calibration_p.toFixed(3)} (below 0.01 would mean the probabilities were off).
        </p>
      )}
      {t.pit && (
        <div>
          <Pit hist={t.pit} />
          <p className="mt-1 text-[11px] text-muted">
            Calibration: flat bars mean the probabilities were right as often as they claimed (largest gap{" "}
            {((t.pit_max_dev ?? 0) * 100).toFixed(1)} pts, allowed {((t.pit_tolerance ?? 0) * 100).toFixed(1)}).
          </p>
        </div>
      )}
      {t.choice && (
        <p className="text-[11px] text-muted">
          {t.choice.kind === "finish"
            ? "Shots × finishing rate"
            : t.choice.kind === "goalie"
              ? "Shots against × save %"
              : t.choice.kind === "game"
                ? "Team shots × finishing × goalie, with OT/shootout rules"
                : "Rate × ice time"}
          {t.choice.kind === "goalie" || t.choice.kind === "game"
            ? ` · save % pulled toward league with ${t.choice.k_sv_shots} shots of weight`
            : ` · recency half-life ${t.choice.half_life_games} games`}
          {t.choice.factors.length > 0 && ` · factors kept: ${t.choice.factors.map((f) => FACTOR_NAMES[f] ?? f).join(", ")}`}
          {" · "}
          {(t.choice.kind === "goalie" ? t.choice.sa_size : t.choice.size) === null ? "Poisson" : "negative binomial"}
        </p>
      )}
      <span className="sr-only">{stat}</span>
    </article>
  );
}

export function Models() {
  const res = useEncrypted<ModelsReport>("models.json");
  if (res.state === "loading") return <Spinner label="Loading model tests…" />;
  if (res.state === "error") return <Notice tone="bad">{res.error.message}</Notice>;
  if (res.state === "unavailable") return <Notice tone="warn">Live data unavailable: no model report published yet.</Notice>;
  const r = res.value.data;
  const stats = Object.entries(r.stats);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">Model tests</h1>
        <p className="text-sm text-muted">
          Each model predicts past games using only what was known before them. Settings are tuned on the earlier
          games, then the model is scored on later games it never saw, against two simple methods: the player's
          season average and his last-10 average. A stat is published only if it beats both by more than chance
          and its probabilities are calibrated.
        </p>
      </header>

      {r.status !== "ok" ? (
        <Notice tone="warn">
          {r.status === "not_run"
            ? "The models haven't run yet."
            : "Not enough completed games loaded to test the models yet, so nothing is projected. This fills in as the backfill loads last season."}
        </Notice>
      ) : (
        <Panel title="Test windows">
          <dl className="num grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs text-muted">History used</dt>
              <dd>
                {r.history ? `${longDate(r.history.from)} – ${longDate(r.history.to)}` : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Tuning</dt>
              <dd>{r.tune ? `${longDate(r.tune.from)} – before ${longDate(r.tune.to_before)}` : "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Test</dt>
              <dd>{r.test ? `${longDate(r.test.from)} – ${longDate(r.test.to)}` : "—"}</dd>
            </div>
          </dl>
          <p className="mt-2 text-xs text-muted">
            Model version {r.version}
            {r.tested_at && ` · tested ${localTime(r.tested_at)}`}. The test assumes the actual starting goalies and
            lineups were known in advance, as they usually are by game time; before confirmation, live projections
            carry that uncertainty and say so.
          </p>
        </Panel>
      )}

      {stats.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {stats.map(([s, t]) => (
            <StatCard key={s} stat={s} t={t} />
          ))}
        </div>
      )}

      <p className="text-xs text-muted">
        Score = average log score per game (higher is better). Differences are per game; the 95% low end must be
        above zero to pass.
      </p>

      {r.not_modeled.length > 0 && (
        <Panel title="Not modeled yet">
          <ul className="text-sm text-muted">
            {r.not_modeled.map((m) => (
              <li key={m.market}>{m.label}: needs a joint saves-and-win model (later)</li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
