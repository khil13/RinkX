import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { XgSummary, ChallengerStat, ModelsReport, StatTest } from "../lib/data/types";
import { longDate, localTime } from "../lib/format";

const BASELINE_LABEL: Record<string, string> = {
  season: "season average",
  l10: "last-10 average",
  home_rate: "home-team win rate",
  log5_record: "standings records (log5)",
  league_rate: "league rate of this outcome",
  independent: "saves and win treated as independent",
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
  playoff: "playoff games",
  po: "playoff games",
  rest: "back-to-backs",
  mates: "linemates",
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
  const required = b.required !== false;
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="text-muted">
        vs {label}
        {!required && " (shown, not required)"}
      </span>
      <span className={`num ${!required ? "text-muted" : better ? "text-over" : "text-bad"}`}>
        {d.mean >= 0 ? "+" : ""}
        {d.mean.toFixed(4)} <span className="text-muted">(95% low {d.lo.toFixed(4)})</span>
      </span>
    </div>
  );
}

function StatCard({ stat, t, chal }: { stat: string; t: StatTest; chal?: ChallengerStat & { version: string } }) {
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
        {t.n_test.toLocaleString()} test {t.unit ?? "games"} · {t.n_tune.toLocaleString()} tuning {t.unit ?? "games"}
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
          {t.choice.slot_w ? ` · line slot's ice time weighted ${(t.choice.slot_w * 100).toFixed(0)}%` : ""}
        </p>
      )}
      {chal && (
        <p className="rounded bg-panel px-2 py-1 text-[11px] text-muted" aria-label="Challenger">
          Challenger v{chal.version}:{" "}
          {chal.log_score != null && t.log_score != null ? (
            <>
              test score {chal.log_score.toFixed(4)} vs {t.log_score.toFixed(4)} published (
              {chal.log_score - t.log_score >= 0 ? "+" : ""}
              {(chal.log_score - t.log_score).toFixed(4)})
            </>
          ) : (
            REASON[chal.reason ?? ""] ?? "not tested"
          )}
          {chal.choice?.factors.length ? ` · factors: ${chal.choice.factors.map((f) => FACTOR_NAMES[f] ?? f).join(", ")}` : ""}
          {chal.choice?.slot_w ? ` · line slot ${(chal.choice.slot_w * 100).toFixed(0)}%` : ""}
        </p>
      )}
      <span className="sr-only">{stat}</span>
    </article>
  );
}

const FAMILY_LABEL: Record<string, string> = {
  skater_shots: "shots",
  skater_scoring: "scoring",
  skater_blocks: "blocks",
  skater_hits: "hits",
  goalie: "goalie",
  game_sim: "game model",
};

function Promotion({ r }: { r: ModelsReport }) {
  const p = r.promotion!;
  return (
    <Panel title="New versions on live results">
      <p className="mb-2 text-xs text-muted">
        A newer version that passes its test runs beside the published one without being shown. Every line priced
        is also priced by it. Once {p.min_props} graded props have been compared, it replaces the published version
        only if its log score is better by more than chance, and is retired if it is worse.
      </p>
      <ul className="flex flex-col gap-1 text-sm" aria-label="Champion and challenger">
        {p.families.map((f) => (
          <li key={f.family} className="flex flex-wrap justify-between gap-2">
            <span>
              {FAMILY_LABEL[f.family] ?? f.family}: v{f.champion ?? "—"} published
              {f.challenger ? `, v${f.challenger} challenging` : ""}
            </span>
            <span className="num text-xs text-muted">
              {!f.challenger
                ? "no challenger"
                : !f.live
                  ? `0 of ${p.min_props} graded props`
                  : `${f.live.n} of ${p.min_props} graded props · ${f.live.mean_diff >= 0 ? "+" : ""}${f.live.mean_diff.toFixed(4)} per prop${
                      f.live.lo != null ? ` (95% ${f.live.lo.toFixed(4)} to ${f.live.hi?.toFixed(4)})` : ""
                    }`}
            </span>
          </li>
        ))}
      </ul>
      {p.history.length > 0 && (
        <ul className="mt-2 text-xs text-muted" aria-label="Promotion history">
          {p.history.map((h) => (
            <li key={`${h.family}-${h.decided_at}`}>
              {localTime(h.decided_at)}: {FAMILY_LABEL[h.family] ?? h.family} v{h.challenger}{" "}
              {h.decision === "promoted" ? `replaced v${h.champion}` : `retired (v${h.champion} kept)`} after{" "}
              {h.n_props} props ({h.mean_diff >= 0 ? "+" : ""}
              {h.mean_diff.toFixed(4)} per prop)
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

const XG_REASON: Record<string, string> = {
  insufficient_history: "not enough shots with locations loaded yet",
  did_not_beat_baseline: "it did not beat the league-average goal rate on the later games",
  miscalibrated: "its probabilities were not calibrated on the later games",
};

function XgPanel({ x }: { x: XgSummary | null }) {
  const m = x?.metrics ?? {};
  return (
    <Panel title="Expected goals (xG)">
      <div className="flex flex-col gap-2 text-sm" aria-label="Expected goals model">
        <p className="text-xs text-muted">
          RinkX's own shot-quality model: the chance an unblocked attempt becomes a goal from its distance, angle,
          shot type, strength and whether it was a rebound. Fit on earlier games, scored on later ones it never saw.
          Used for player xG, goals saved above expected and model 1.7's finishing only if it passes.
        </p>
        {!x ? (
          <p className="text-muted">Not fit yet: DATA UNAVAILABLE.</p>
        ) : (
          <>
            <p>
              <span className={x.passed ? "text-over" : "text-warn"}>{x.passed ? "Passed" : "Not used"}</span>
              {!x.passed && x.reason && <span className="text-muted">: {XG_REASON[x.reason] ?? x.reason}</span>}
              <span className="text-xs text-muted">
                {" "}
                · fit {localTime(x.fitted_at)} on {x.n_train.toLocaleString()} attempts
                {x.train[0] && ` (${longDate(x.train[0])} – ${longDate(x.train[1]!)})`}, tested on{" "}
                {x.n_test.toLocaleString()}
                {x.test[0] && ` (${longDate(x.test[0])} – ${longDate(x.test[1]!)})`}
              </span>
            </p>
            {m.log_loss !== undefined && (
              <dl className="num grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                <div>
                  <dt className="font-sans text-muted">Log loss</dt>
                  <dd>{m.log_loss.toFixed(4)}</dd>
                </div>
                <div>
                  <dt className="font-sans text-muted">League rate only</dt>
                  <dd>{m.baseline_log_loss?.toFixed(4)}</dd>
                </div>
                <div>
                  <dt className="font-sans text-muted">Distance and angle only</dt>
                  <dd>{m.distance_only_log_loss?.toFixed(4)}</dd>
                </div>
                <div>
                  <dt className="font-sans text-muted">AUC · calibration gap</dt>
                  <dd>
                    {m.auc?.toFixed(3) ?? "—"} · {m.ece !== undefined ? `${(m.ece * 100).toFixed(1)} pts` : "—"}
                  </dd>
                </div>
              </dl>
            )}
            {m.goal_rate_test !== undefined && (
              <p className="text-xs text-muted">
                Later games: {(m.goal_rate_test * 100).toFixed(1)}% of attempts were goals; the model expected{" "}
                {(m.mean_xg_test! * 100).toFixed(1)}%. Lower log loss is better.
              </p>
            )}
            {!x.passed && x.in_use && (
              <p className="text-xs text-muted">Still using the fit from {localTime(x.in_use.fitted_at)}, which passed.</p>
            )}
          </>
        )}
      </div>
    </Panel>
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
            Published versions:{" "}
            {Object.entries(r.versions ?? {})
              .map(([f, v]) => `${FAMILY_LABEL[f] ?? f} v${v}`)
              .join(", ") || r.version}
            {r.tested_at && ` · tested ${localTime(r.tested_at)}`}. The test assumes the actual starting goalies were
            known in advance, as they usually are by game time; lines and power-play units are each player's from his
            previous game, as they are live until a Quick Entry says otherwise.
          </p>
        </Panel>
      )}

      {stats.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {stats.map(([s, t]) => {
            const c = r.challengers?.[t.family];
            const cs = c?.stats[s];
            return <StatCard key={s} stat={s} t={t} chal={cs ? { ...cs, version: c.version } : undefined} />;
          })}
        </div>
      )}

      <p className="text-xs text-muted">
        Score = average log score per game (higher is better). Differences are per game; the 95% low end must be
        above zero to pass.
      </p>

      {r.promotion && r.promotion.families.length > 0 && <Promotion r={r} />}

      <XgPanel x={r.xg ?? null} />

      {r.not_modeled.length > 0 && (
        <Panel title="Not modeled yet">
          <ul className="text-sm text-muted">
            {r.not_modeled.map((m) => (
              <li key={m.market}>{m.label}: not modeled</li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
