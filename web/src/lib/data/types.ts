// Mirrors pipeline/rinkx/publish/schemas.py (the published data contract, docs/03-api.md).

export type DataStatus = "live" | "stale" | "unavailable" | "synthetic";
export type FeedState = "ok" | "stale" | "failed" | "unavailable";

export interface FeedStatus {
  code: string;
  name: string;
  state: FeedState;
  last_success_at: string | null;
  reason: string | null;
}

export interface FileEntry {
  sha256: string;
  bytes: number;
}

export interface BuildInfo {
  app_version: string;
  git_sha: string | null;
  run_url: string | null;
}

export interface Manifest {
  schema_version: number;
  generated_at: string;
  env: "dev" | "prod";
  configured: boolean;
  missing_setup: string[];
  slate_date: string;
  build: BuildInfo;
  feeds: FeedStatus[];
  files: Record<string, FileEntry>;
}

export interface Meta {
  generated_at: string;
  data_status: DataStatus;
  oldest_input_at: string | null;
  sources: string[];
  model_versions: Record<string, string>;
}

export interface Envelope<T> {
  data: T;
  meta: Meta;
}

export interface HealthData {
  build: BuildInfo;
  store: { asset: string | null; schema_version: number };
  table_rows: Record<string, number>;
  synthetic_rows: Record<string, number>;
  data_sources: { code: string; name: string; category: string; tier: string; is_enabled: number }[];
  recent_runs: {
    job_name: string;
    source: string;
    status: string;
    started_at: string | null;
    finished_at: string | null;
    rows_upserted: number | null;
    quota_remaining: number | null;
    error: string | null;
  }[];
}

// ---- Phase 1: league data (pipeline/rinkx/publish/views.py) ----

/** Why a value is null. Never shown as zero. */
export type Reason = "not_connected" | "data_unavailable" | "insufficient_sample" | "not_final" | "no_games_this_season";

export interface TeamInfo {
  abbrev: string;
  name: string;
  location: string;
  conference: string | null;
  division: string | null;
}

export interface Record_ {
  as_of: string;
  gp: number;
  w: number;
  l: number;
  otl: number;
  pts: number;
  gf: number;
  ga: number;
  l10: string | null;
  streak: string | null;
}

export interface ScheduleContext {
  /** null when unknown (season schedule not fully loaded) */
  first_game_of_season: boolean | null;
  rest_days: number | null;
  back_to_back: boolean;
  games_last_7d: number;
  travel_km: number | null;
  tz_shift_hours: number | null;
}

export interface TeamMetrics {
  games: number;
  goals_for_pg: number;
  goals_against_pg: number;
  shots_for_pg: number;
  shots_against_pg: number;
}

export interface RosterPlayer {
  id: number;
  name: string;
  position: string;
  number: number | null;
}

export interface Side {
  team: TeamInfo;
  score: number | null;
  record: Record_ | null;
  record_reason: Reason | null;
  context: ScheduleContext | null;
  context_reason: Reason | null;
  /** Upcoming games: the projected or Quick-Entry-confirmed starter. */
  goalie: GoalieStart | null;
  goalie_reason: Reason | null;
  injuries: null;
  injuries_reason: Reason;
  // game detail only
  metrics?: TeamMetrics | null;
  metrics_reason?: Reason | null;
  roster?: RosterPlayer[];
  lines?: null;
  lines_reason?: Reason;
}

export type GameStatus = "scheduled" | "pregame" | "live" | "final" | "postponed" | "cancelled";

export interface GameSummary {
  id: number;
  season: number;
  game_type: "P" | "R" | "O";
  date: string;
  start_time_utc: string;
  status: GameStatus;
  period: number | null;
  ended_in: "REG" | "OT" | "SO" | null;
  venue: string | null;
  neutral_site: boolean;
  home: Side;
  away: Side;
  environment: null;
  environment_reason: Reason;
  /** Model-only game outlook (no odds); null when not projected or not tested yet. */
  model: { p_home_win: number | null; goals_home: number | null; goals_away: number | null } | null;
  fetched_at: string;
}

export interface SkaterLine {
  id: number;
  name: string;
  position: string;
  team: string;
  toi_s: number | null;
  g: number | null;
  a: number | null;
  p: number | null;
  sog: number | null;
  hits: number | null;
  blk: number | null;
  pim: number | null;
  pm: number | null;
  ppg: number | null;
}

export interface GoalieLine {
  id: number;
  name: string;
  team: string;
  started: boolean;
  toi_s: number | null;
  sa: number | null;
  sv: number | null;
  ga: number | null;
  decision: "W" | "L" | "O" | null;
}

export interface GameDetail extends GameSummary {
  boxscore: { skaters: SkaterLine[]; goalies: GoalieLine[] } | null;
  boxscore_reason: Reason | null;
  projections: GameProjections;
}

export interface Slate {
  date: string;
  games: GameSummary[];
}

export interface PlayerIndexEntry {
  id: number;
  name: string;
  position: string;
  number: number | null;
  team: string | null;
}

export interface PlayerBio {
  id: number;
  name: string;
  position: string;
  number: number | null;
  team: string | null;
  shoots_catches: string | null;
  birth_date: string | null;
  height_cm: number | null;
  weight_kg: number | null;
}

export interface SkaterGame {
  game_id: number;
  date: string;
  season: number;
  playoffs?: boolean;
  opponent: string;
  home: boolean;
  dnp?: false;
  toi_s: number | null;
  ev_toi_s: number | null;
  pp_toi_s: number | null;
  sh_toi_s: number | null;
  g: number | null;
  a: number | null;
  p: number | null;
  a1: number | null;
  a2: number | null;
  sog: number | null;
  icf: number | null;
  hits: number | null;
  blk: number | null;
  pim: number | null;
  pm: number | null;
  ppg: number | null;
  ppa: number | null;
  ppp: number | null;
  fow: number | null;
  fol: number | null;
}

export interface GoalieGame {
  game_id: number;
  date: string;
  season: number;
  playoffs?: boolean;
  opponent: string;
  home: boolean;
  dnp?: false;
  started: boolean;
  toi_s: number | null;
  sa: number | null;
  sv: number | null;
  ga: number | null;
  decision: "W" | "L" | "O" | null;
  shutout: boolean | null;
}

/** A completed team game the player did not play: listed, never counted in hit rates. */
export interface DnpGame {
  game_id: number;
  date: string;
  season: number;
  opponent: string;
  home: boolean;
  dnp: true;
}

export interface HitWindow {
  games: number;
  known: number;
  missing: number;
  counts: number[];
  mean: number | null;
  median: number | null;
  sd: number | null;
  from: string | null;
  to: string | null;
}

export type WindowName = "L5" | "L10" | "L15" | "L20" | "season" | "last_season";

export interface HitRateStat {
  label: string;
  thresholds: number[];
  windows: Record<WindowName, HitWindow>;
}

export interface PlayerPage {
  player: PlayerBio;
  season: number | null;
  last_season: number | null;
  totals: Record<string, number | null> | null;
  totals_reason: Reason | null;
  games: (SkaterGame | GoalieGame | DnpGame)[];
  last_season_games: (SkaterGame | GoalieGame)[];
  hit_rates: Record<string, HitRateStat>;
  hit_rates_basis: "games_played" | "starts";
  projection: PlayerProjection;
}

// ---- Phase 3: projections (pipeline/rinkx/publish/projections.py) ----

export interface GoalieStart {
  id: number;
  name: string;
  /** confirmed = Quick Entry with a source; projected = from recent starts; actual = box score */
  status: "confirmed" | "projected" | "unknown" | "actual";
  probability: number;
  source: string | null;
  reported_at: string | null;
}

export interface Factor {
  name: string;
  /** multiplicative effect on the projected mean, e.g. 0.08 = +8% */
  effect: number;
  detail: string;
}

export interface MarketProjection {
  market: string;
  label: string;
  kind: "over_under" | "yes_no";
  stat: string;
  mean: number;
  median: number | null;
  sd: number | null;
  /** P(X >= k) for k = 0.. */
  p_ge: number[];
  data_quality: number;
  missing_inputs: string[];
  computed_at: string;
  trigger_reason: string;
  previous: { mean: number; computed_at: string; reason: string } | null;
  // player page only
  pmf?: number[];
  factors_for?: Factor[];
  factors_against?: Factor[];
  inputs?: Record<string, string | number | null>;
  as_of?: string;
}

export type ModelReason =
  | "models_not_ready"
  | "no_model_passed"
  | "game_started"
  | "outside_window"
  | "ruled_out"
  | "no_upcoming_projection";

export interface ModelStatus {
  status: "ok" | "insufficient_history" | "not_run" | null;
  tested_at: string | null;
  passed: string[];
}

export interface ProjectedPlayer {
  id: number;
  name: string;
  position: string;
  toi_s: number | null;
  pp_toi_s: number | null;
  markets: Record<string, MarketProjection>;
}

export interface SideProjections {
  goalie: GoalieStart | null;
  players: ProjectedPlayer[];
  out: { id: number; name: string; reason: string | null; source: string }[];
}

export interface TotalProjection {
  mean: number;
  p_ge: number[];
  factors: Factor[];
  reference_mean: number | null;
}

export interface GameEnvironment {
  win: {
    home: number;
    away: number;
    tied_after_regulation: number | null;
    expected_goals: { home: number | null; away: number | null };
    goalies: Record<string, string> | null;
    factors: Factor[];
    previous: { home: number; reason: string } | null;
  } | null;
  totals: Partial<Record<"home" | "away" | "game", TotalProjection>>;
  computed_at: string;
}

export interface GameProjections {
  models: ModelStatus;
  reason: ModelReason | null;
  environment: GameEnvironment | null;
  home: SideProjections;
  away: SideProjections;
}

export interface PlayerProjection {
  models: ModelStatus;
  game: { id: number; date: string; start_time_utc: string; home: boolean; opponent: string } | null;
  markets: MarketProjection[];
  reason: ModelReason | null;
}

export interface Baseline {
  log_score: number;
  model_minus_baseline: { mean: number; se: number; lo: number };
}

export interface StatTest {
  label: string;
  family: string;
  /** what one test row is: player-games, goalie starts, team-games, games */
  unit?: string;
  n_tune: number;
  n_test: number;
  passed: boolean;
  reason: "did_not_beat_baselines" | "miscalibrated" | "insufficient_history" | null;
  log_score?: number;
  baselines?: Record<string, Baseline>;
  /** binary stats: Hosmer-Lemeshow p-value (small = miscalibrated) */
  calibration_p?: number;
  pit?: number[];
  pit_max_dev?: number;
  pit_tolerance?: number;
  mean_pred?: number;
  mean_actual?: number;
  choice?: {
    kind: string;
    factors: string[];
    half_life_games?: number;
    m?: number;
    size: number | null;
    sa_size?: number | null;
    k_sv_shots: number;
    fin_prior_shots?: number;
  } | null;
}

export interface ModelsReport {
  version: string;
  status: "ok" | "insufficient_history" | "not_run";
  tested_at: string | null;
  history?: { from: string; to: string } | null;
  tune?: { from: string; to_before: string } | null;
  test?: { from: string; to: string } | null;
  lineup_mode?: string;
  stats: Record<string, StatTest>;
  markets: Record<string, { stat: string; label: string }>;
  not_modeled: { market: string; label: string; reason: string }[];
}
