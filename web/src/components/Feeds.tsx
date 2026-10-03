import type { FeedStatus } from "../lib/data/types";
import { ago, localTime } from "../lib/format";
import { DataChip, Panel } from "./ui";

/** Status of each data feed from the site manifest. `only` narrows the list (Today shows only
 * the feeds that aren't ok; Admin shows them all). */
export function FeedsPanel({ feeds, generatedAt, only }: { feeds: FeedStatus[]; generatedAt: string; only?: (f: FeedStatus) => boolean }) {
  const shown = only ? feeds.filter(only) : feeds;
  if (shown.length === 0) return null;
  return (
    <Panel title="Data feeds" right={<span className="text-xs text-muted">as of {ago(generatedAt)}</span>}>
      <ul className="divide-y divide-line" aria-label="Data feeds">
        {shown.map((f) => (
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
  );
}
