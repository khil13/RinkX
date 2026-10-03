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

export interface OddsAdmin {
  credits: { remaining: number | null; used: number | null; as_of: string } | null;
  monthly_credits: number | null;
  reserve: number | null;
  last_plan: { allowance: number; remaining: number; game_lines: boolean; prop_games: number;
               unmapped_markets: string[]; at: string } | null;
  unresolved_players: {
    name: string;
    reason: string;
    game_id: number;
    suggestion: { nhl_id: number; name: string } | null;
    detected_at: string;
  }[];
}

export interface HealthData {
  odds?: OddsAdmin;
  injuries?: {
    status: string;
    at: string | null;
    detail: { listed?: number; changed?: number; resolved?: number; unmatched?: number; checked_at?: string };
    error: string | null;
    active: number;
    unmatched: { name: string; suggestion: { nhl_id: number; name: string } | null }[];
  } | null;
  build: BuildInfo;
  store: {
    asset: string | null;
    schema_version: number;
    /** versions found at the start of the run, newest first (Phase 10) */
    versions?: { name: string; at: string; kind: "recent" | "weekly" }[];
    keep?: { recent: number; weekly: number };
    drill?: {
      status: string;
      at: string | null;
      detail: { checked_at?: string; version?: string | null; age_days?: number | null; integrity?: string; schema?: number; note?: string; error?: string; rows?: Record<string, number> };
      error: string | null;
    } | null;
  };
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
export type Reason =
  | "not_connected"
  | "data_unavailable"
  | "insufficient_sample"
  | "not_final"
  | "no_games_this_season"
  | "past_game"
  | "stale";

/** One player on the injury report (ESPN, unofficial). */
export interface InjuryEntry {
  player: { id: number; name: string; position: string };
  status: "out" | "injured_reserve" | "long_term_ir" | "day_to_day" | "questionable" | "suspended" | "unknown";
  body_part: string | null;
  description: string | null;
  expected_return: string | null;
  reported_at: string;
  source: string;
  source_name: string;
}

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
  /** upcoming games: the team's injury report; null with a reason otherwise */
  injuries: InjuryEntry[] | null;
  injuries_reason: Reason | null;
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
  /** open sportsbook lines for this game (Phase 4) */
  line_count: number;
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
  lines: GameLines;
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
  lines: { game: { id: number; date: string }; markets: LineMarket[]; events?: LineEvent[] } | null;
  news?: NewsItem[];
  injury?: InjuryEntry | null;
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

// ---- Phase 4: sportsbook lines (pipeline/rinkx/publish/lines.py) ----

export interface LineMove {
  at: string;
  line: number | null;
  over: number | null;
  under: number | null;
  status: string;
}

export interface BookLine {
  book: string;
  book_name: string;
  line: number | null;
  /** over / yes / home price (American) */
  over: number | null;
  /** under / no / away price */
  under: number | null;
  last_seen_at: string;
  last_changed_at: string;
  movement?: LineMove[];
  pricing: Pricing | null;
}

/** A frozen model-vs-market prediction for one line at one book (Phase 5). */
export interface Pricing {
  prediction_id: number;
  /** model probability of over/yes/home, without pushes */
  p_model_over: number;
  p_model_under: number;
  p_push: number;
  p_novig_over: number | null;
  p_implied_over: number | null;
  edge_over: number | null;
  edge_under: number | null;
  ev_over: number | null;
  ev_under: number | null;
  side: "over" | "under" | "yes" | "no" | "home" | "away" | "none";
  confidence: number | null;
  devig_method: string | null;
  priced_at: string;
  // player page only
  confidence_parts?: {
    score: number;
    parts: Record<string, number>;
    max: Record<string, number>;
    notes: Record<string, string[]>;
  };
  calculation?: string[];
}

export interface Lean {
  book: string;
  book_name: string;
  side: Pricing["side"];
  line: number | null;
  price: number;
  edge: number;
  ev: number;
  confidence: number | null;
  p_model: number;
}

export interface LineRow {
  player: { id: number; name: string } | null;
  team: string | null;
  books: BookLine[];
  line: number | null;
  best_over: { book: string; price: number } | null;
  best_under: { book: string; price: number } | null;
  /** median no-vig probability of over/yes/home across books at the common line */
  consensus: { p_over: number; books: number } | null;
  consensus_reason: "vig_not_removable" | null;
  lines_differ: boolean;
  lean: Lean | null;
}

export interface LineMarket {
  market: string;
  label: string;
  kind: "over_under" | "yes_no" | "moneyline";
  rows: LineRow[];
}

export interface GameLines {
  books: string[];
  markets: LineMarket[];
  fetched_at: string | null;
  reason: "not_connected" | "no_lines_yet" | null;
}

// ---- Phase 6: Best Props (pipeline/rinkx/publish/best.py) ----

export interface PropRow {
  prediction_id: number;
  subject:
    | { type: "player"; id: number; name: string; team: string | null; position: string }
    | { type: "game"; name: string; team: null };
  game: { id: number; date: string; start_time_utc: string; home: string; away: string };
  market: string;
  market_label: string;
  kind: "over_under" | "yes_no" | "moneyline";
  book: string;
  book_name: string;
  source: string;
  line: number | null;
  over_price: number | null;
  under_price: number | null;
  /** the side RinkX leans, or null (no lean) */
  lean: "over" | "under" | "yes" | "no" | "home" | "away" | null;
  /** the side the numbers below describe (the lean, or the better side) */
  side_scored: string;
  price: number | null;
  p_model: number;
  p_market: number | null;
  market_is_novig: boolean;
  edge: number | null;
  ev: number | null;
  confidence: number | null;
  confidence_parts: NonNullable<Pricing["confidence_parts"]> | null;
  calculation: string[];
  data_quality: number | null;
  missing_inputs: string[];
  line_seen_at: string;
  line_changed_at: string;
  priced_at: string;
}

export interface BestProps {
  generated_at: string;
  books: string[];
  open_lines: number;
  rows: PropRow[];
  reason: "not_connected" | "no_lines" | "not_priced" | null;
}

// ---- Model Performance (Phase 7) ----------------------------------------------------------------

export interface BetRecord {
  n: number;
  wins: number;
  losses: number;
  pushes: number;
  profit: number;
  roi: number | null;
  roi_ci?: [number, number];
  clv_n: number;
  avg_clv: number | null;
  beat_close: number | null;
  expected_roi: number | null;
}

export interface ProbScores {
  brier: number;
  log_loss: number;
}

export interface GradedBet {
  prediction_id: number;
  date: string;
  game: string;
  subject: string;
  player_id: number | null;
  market_label: string;
  line: number | null;
  side: string;
  price: number | null;
  book_name: string | null;
  confidence: number | null;
  p_model: number;
  actual: number | null;
  outcome: "win" | "loss" | "push" | "void";
  profit: number | null;
  clv: number | null;
  void_reason: string | null;
}

export interface Performance {
  generated_at: string;
  lineup_mode: "live_tracked";
  period: { from: string; to: string } | null;
  graded: number;
  synthetic: boolean;
  min_bets: number;
  calibration: {
    n: number;
    model?: ProbScores;
    market?: ProbScores;
    model_same_rows?: ProbScores;
    n_vs_market?: number;
    reliability?: { lo: number; hi: number; n: number; mean_p: number; hit_rate: number }[];
    ece?: number | null;
    mean_p?: number;
    hit_rate?: number;
  };
  bets: BetRecord;
  series: { date: string; profit: number; cumulative: number; bets: number }[];
  max_drawdown: number;
  by_market: (BetRecord & { key: string })[];
  by_confidence: (BetRecord & { key: string })[];
  confidence_monotonic: boolean | null;
  by_month: (BetRecord & { key: string })[];
  by_version: (BetRecord & { key: string })[];
  voids: Record<string, number>;
  recent: GradedBet[];
  /** live isotonic calibrators by market */
  calibrators?: {
    market: string;
    applied: boolean;
    reason: "applied" | "no_improvement" | "too_few";
    n_fit: number;
    n_holdout: number;
    brier_raw: number | null;
    brier_cal: number | null;
    fitted_at: string;
  }[];
}

// ---- News & alerts (Phase 8) ----------------------------------------------------------------------

export interface NewsItem {
  id: number;
  headline: string;
  summary: string | null;
  url: string;
  category: "injury" | "lineup" | "goalie" | "scratch" | "suspension" | "coach" | "rest" | "transaction" | "general";
  reliability: "official" | "beat_reporter" | "aggregator" | "unverified";
  published_at: string;
  player: { id: number; name: string } | null;
  team: string | null;
}

export interface NewsFeed {
  generated_at: string;
  days: number;
  items: NewsItem[];
}

export interface AlertsHistory {
  generated_at: string;
  delivery: "ntfy" | null;
  alerts: { key: string; type: string; active: boolean; condition: Record<string, unknown>; last_triggered_at: string | null }[];
  events: {
    key: string;
    type: string;
    game: number;
    matchup: string;
    message: string;
    matches: number;
    triggered_at: string;
    delivery: "pending" | "sent" | "failed";
    delivered_at: string | null;
  }[];
}

// ---- Line movement board (Phase 10) ----------------------------------------------------------------

export interface MovementRow {
  subject: string;
  player_id: number | null;
  team: string | null;
  game: { id: number; home: string; away: string; start_time_utc: string };
  market: string;
  market_label: string;
  kind: "over_under" | "yes_no" | "moneyline";
  book_name: string;
  line: number | null;
  first: { over: number | null; under: number | null; at: string };
  now: { over: number | null; under: number | null; at: string };
  changed_at: string;
  moves: number;
  /** change in the over / yes / home side's implied probability, points */
  change_pts: number | null;
}

export interface MovementBoard {
  generated_at: string;
  source: string;
  rows: MovementRow[];
}

/** Something that happened while a line was moving (marker on the movement chart). */
export interface LineEvent {
  at: string;
  kind: "goalie" | "availability" | "news" | "injury";
  label: string;
  source: string | null;
}
