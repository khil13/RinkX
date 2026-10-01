import { DataChip, Notice, Panel } from "../components/ui";
import { useManifest } from "../lib/data/fetch";
import { ago, localTime } from "../lib/format";

export function Dashboard() {
  const manifest = useManifest().data;
  if (!manifest) return null;
  const connected = manifest.feeds.some((f) => f.state !== "unavailable");

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <h1 className="text-lg font-semibold">Dashboard</h1>

      {!connected && (
        <Notice tone="warn">
          Live data unavailable. No data sources are connected yet, so there is no slate, no projections and no
          lines to show. The NHL schedule and game pages arrive in Phase 1.
        </Notice>
      )}

      <Panel title="Data feeds" right={<span className="text-xs text-muted">as of {ago(manifest.generated_at)}</span>}>
        <ul className="divide-y divide-line">
          {manifest.feeds.map((f) => (
            <li key={f.code} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
              <div>
                <div className="text-sm">{f.name}</div>
                <div className="text-xs text-muted">
                  {f.last_success_at ? `Last success ${localTime(f.last_success_at)}` : "Never fetched"}
                  {f.reason && ` · ${f.reason}`}
                </div>
              </div>
              <DataChip state={f.state} detail={f.last_success_at ? ago(f.last_success_at) : undefined} />
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Site">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
          <dt className="text-muted">Published</dt>
          <dd className="num">{localTime(manifest.generated_at)}</dd>
          <dt className="text-muted">Build</dt>
          <dd className="num">
            v{manifest.build.app_version}
            {manifest.build.git_sha && ` · ${manifest.build.git_sha.slice(0, 7)}`}
          </dd>
          <dt className="text-muted">Environment</dt>
          <dd className="num">{manifest.env}</dd>
        </dl>
      </Panel>
    </div>
  );
}
