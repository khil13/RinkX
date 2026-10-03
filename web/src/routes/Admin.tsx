import { DataChip, Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { HealthData } from "../lib/data/types";
import { localTime } from "../lib/format";

function StoreBackups({ store }: { store: HealthData["store"] }) {
  const versions = store.versions ?? [];
  const drill = store.drill;
  const weekly = versions.filter((v) => v.kind === "weekly");
  return (
    <Panel title="Store & backups">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
        <dt className="text-muted">Versions kept</dt>
        <dd className="num">
          {versions.length} ({versions.length - weekly.length} recent, {weekly.length} weekly)
          {store.keep && (
            <span className="text-muted">
              {" "}
              · policy: newest {store.keep.recent} plus one per week for {store.keep.weekly} weeks
            </span>
          )}
        </dd>
        <dt className="text-muted">Oldest</dt>
        <dd className="num">{versions.length ? localTime(versions[versions.length - 1]!.at) : "—"}</dd>
        <dt className="text-muted">Restore drill</dt>
        <dd>
          {!drill ? (
            <span className="text-muted">not run yet (runs about once a day)</span>
          ) : drill.status === "succeeded" ? (
            <span className="text-over">
              passed {drill.at ? localTime(drill.at) : ""}
              <span className="num text-muted">
                {drill.detail.version
                  ? ` · oldest version (${drill.detail.age_days?.toFixed(1)} days old) decrypted, integrity ${drill.detail.integrity}, migrated to schema v${drill.detail.schema}`
                  : ` · ${drill.detail.note ?? ""}`}
              </span>
            </span>
          ) : (
            <span className="text-bad">
              FAILED {drill.at ? localTime(drill.at) : ""} · {drill.detail.error ?? drill.error ?? "see the run log"}
            </span>
          )}
        </dd>
      </dl>
      <p className="mt-2 text-[11px] text-muted">
        To roll back: Actions → store → Run workflow → restore, with a version name from “list”. It uploads that
        version as the newest and deletes nothing. Keep offline copies of STORE_KEY and DATA_KEY in your password
        manager: without STORE_KEY no backup can be read.
      </p>
    </Panel>
  );
}

export function Admin() {
  const health = useEncrypted<HealthData>("admin/health.json");

  if (health.state === "loading") return <Spinner label="Decrypting health report…" />;
  if (health.state === "unavailable") return <Notice tone="warn">Health report unavailable in this build.</Notice>;
  if (health.state === "error") return <Notice tone="bad">{health.error.message}</Notice>;

  const { data, meta } = health.value;
  const rows = Object.entries(data.table_rows).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Admin</h1>
        {meta.data_status === "synthetic" && <DataChip state="synthetic" />}
      </div>

      {Object.keys(data.synthetic_rows).length > 0 && (
        <Notice tone="warn">
          Store contains SYNTHETIC rows:{" "}
          <span className="num">
            {Object.entries(data.synthetic_rows)
              .map(([t, n]) => `${t}=${n}`)
              .join(", ")}
          </span>
          . Production publishing is blocked while they exist.
        </Notice>
      )}

      <Panel title="Pipeline">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
          <dt className="text-muted">Store version</dt>
          <dd className="num break-all">{data.store.asset ?? "new store (first run)"}</dd>
          <dt className="text-muted">Schema</dt>
          <dd className="num">v{data.store.schema_version}</dd>
          <dt className="text-muted">Report generated</dt>
          <dd className="num">{localTime(meta.generated_at)}</dd>
          <dt className="text-muted">Run log</dt>
          <dd>
            {data.build.run_url ? (
              <a className="text-accent underline" href={data.build.run_url}>
                GitHub Actions run
              </a>
            ) : (
              <span className="text-muted">local run</span>
            )}
          </dd>
        </dl>
      </Panel>

      <StoreBackups store={data.store} />

      <Panel title="Injury report (ESPN)">
        {!data.injuries ? (
          <p className="text-sm text-muted">Not fetched yet. The pipeline reads it on every run.</p>
        ) : (
          <div className="flex flex-col gap-2 text-sm">
            <p>
              Last check {data.injuries.at ? localTime(data.injuries.at) : "—"} ·{" "}
              <span className={data.injuries.status === "failed" ? "text-bad" : ""}>{data.injuries.status}</span> ·{" "}
              <span className="num">
                {data.injuries.detail.listed ?? 0} listed, {data.injuries.active} matched and active,{" "}
                {data.injuries.unmatched.length} unmatched
              </span>
            </p>
            {data.injuries.error && <p className="text-xs text-bad">{data.injuries.error}</p>}
            {data.injuries.unmatched.length > 0 && (
              <details>
                <summary className="cursor-pointer text-xs text-muted">
                  Names that didn't match an NHL player (add them to config/player_aliases.yml)
                </summary>
                <ul className="mt-1 text-xs">
                  {data.injuries.unmatched.map((u) => (
                    <li key={u.name}>
                      {u.name}
                      {u.suggestion && (
                        <span className="text-muted">
                          {" "}
                          · maybe {u.suggestion.name} ({u.suggestion.nhl_id})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </Panel>

      <Panel title="Sportsbook lines (The Odds API)">
        {!data.odds || (!data.odds.credits && !data.odds.last_plan) ? (
          <p className="text-sm text-muted">
            Not connected. Add the ODDS_API_KEY repository secret; the next pipeline run starts fetching lines.
          </p>
        ) : (
          <div className="flex flex-col gap-3 text-sm">
            <dl className="num grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
              <dt className="font-sans text-muted">Credits remaining</dt>
              <dd>
                {data.odds.credits?.remaining ?? "—"} of {data.odds.monthly_credits ?? "—"} / month (reserve{" "}
                {data.odds.reserve ?? "—"})
              </dd>
              {data.odds.last_plan && (
                <>
                  <dt className="font-sans text-muted">Last run</dt>
                  <dd>
                    allowance {data.odds.last_plan.allowance} · game lines {data.odds.last_plan.game_lines ? "yes" : "no"}{" "}
                    · props for {data.odds.last_plan.prop_games} game(s)
                  </dd>
                </>
              )}
            </dl>
            {data.odds.last_plan && data.odds.last_plan.unmapped_markets.length > 0 && (
              <Notice tone="warn">
                Market keys not in config/odds_markets.yml: {data.odds.last_plan.unmapped_markets.join(", ")}
              </Notice>
            )}
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted">
                Unmatched player names ({data.odds.unresolved_players.length}): their lines are hidden
              </h3>
              {data.odds.unresolved_players.length === 0 ? (
                <p className="text-muted">None.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {data.odds.unresolved_players.map((u) => (
                    <li key={u.name} className="py-1.5">
                      <span className="font-semibold">{u.name}</span>{" "}
                      <span className="text-muted">
                        {u.reason === "ambiguous" ? "matches more than one player" : "no match on either roster"}
                        {u.suggestion && ` · maybe ${u.suggestion.name} (NHL id ${u.suggestion.nhl_id})`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-1 text-xs text-muted">
                To fix one, add <code className="num">"Book Name": nhl_id</code> under <code>aliases</code> in
                config/player_aliases.yml.
              </p>
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Data sources">
        {data.data_sources.length === 0 ? (
          <p className="text-sm text-muted">None registered yet. Phase 1 adds the NHL API.</p>
        ) : (
          <ul className="divide-y divide-line text-sm">
            {data.data_sources.map((s) => (
              <li key={s.code} className="flex justify-between py-2">
                <span>{s.name}</span>
                <span className="num text-muted">
                  {s.category} · {s.tier} · {s.is_enabled ? "enabled" : "disabled"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Recent ingestion runs">
        {data.recent_runs.length === 0 ? (
          <p className="text-sm text-muted">No ingestion runs yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr>
                  <th className="py-1 pr-3">Job</th>
                  <th className="py-1 pr-3">Status</th>
                  <th className="py-1 pr-3">Started</th>
                  <th className="py-1">Rows</th>
                </tr>
              </thead>
              <tbody className="num">
                {data.recent_runs.map((r, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-1.5 pr-3">{r.job_name}</td>
                    <td className="py-1.5 pr-3">{r.status}</td>
                    <td className="py-1.5 pr-3">{r.started_at ? localTime(r.started_at) : "—"}</td>
                    <td className="py-1.5">{r.rows_upserted ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Store tables (rows)">
        <ul className="num grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {rows.map(([t, n]) => (
            <li key={t} className="flex justify-between">
              <span className={n ? "" : "text-muted"}>{t}</span>
              <span className={n ? "" : "text-muted"}>{n}</span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
