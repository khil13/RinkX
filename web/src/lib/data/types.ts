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
  goalie: null;
  goalie_reason: Reason;
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
}
