import { DataChip, Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { HealthData } from "../lib/data/types";
import { localTime } from "../lib/format";

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
