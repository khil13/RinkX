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
