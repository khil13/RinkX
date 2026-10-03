import { useState } from "react";
import { Link } from "react-router";
import { Freshness } from "../components/Freshness";
import { TeamChip } from "../components/Team";
import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { Deployment, DeploymentPlayer } from "../lib/data/types";
import { clock, localTime, longDate, mmss } from "../lib/format";

const slot = (line: string | null, pp: number | null) => `${line ?? "—"}${pp ? ` · PP${pp}` : ""}`;

function Row({ p, text }: { p: DeploymentPlayer; text: Record<string, string> }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-sm">
      <span className="flex flex-wrap items-center gap-1.5">
        <TeamChip abbrev={p.team} />
        <Link to={`/players/${p.id}`} className="font-semibold hover:text-accent">
          {p.name}
        </Link>
        <span className="num">{slot(p.line, p.pp_unit)}</span>
        {p.changes.map((c) => (
          <span key={c} className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${c.endsWith("promotion") || c === "toi_up" ? "border-over/50 text-over" : "border-warn/50 text-warn"}`}>
            {text[c] ?? c}
          </span>
        ))}
      </span>
      <span className="num text-[11px] text-muted">
        {p.previous && `was ${slot(p.previous.line, p.previous.pp_unit)} · `}
        TOI exp {mmss(p.expected_toi_s)} / L5 {mmss(p.recent_toi_s)} ·{" "}
        {p.status === "quick_entry" ? (
          <>
            Quick Entry{" "}
            {p.source && (
              <a href={p.source} target="_blank" rel="noreferrer" className="underline">
                source
              </a>
            )}
          </>
        ) : (
          "last game (shift chart)"
        )}
      </span>
    </li>
  );
}

export function DeploymentPage() {
  const res = useEncrypted<Deployment>("deployment.json");
  const [onlyChanges, setOnlyChanges] = useState(true);
  if (res.state === "loading") return <Spinner label="Loading lines…" />;
  if (res.state !== "ready") return <Notice tone="warn">DATA UNAVAILABLE: no deployment file published yet.</Notice>;
  const d = res.value.data;
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold">Lines & power play</h1>
        <p className="text-sm text-muted">
          Each skater's line and PP unit for upcoming games: his last game's (reconstructed from the NHL shift chart),
          or a Quick Entry line change with its source. Changes are against his previous game; projections use the
          same deployment. Lineups aren't confirmed until a source says so.
        </p>
        <Freshness at={res.value.meta.generated_at} staleAfterMin={120} source="RinkX pipeline" />
        <p className="text-[11px] text-muted">
          Shift charts last read {d.lines_updated_at ? localTime(d.lines_updated_at) : "never (DATA UNAVAILABLE)"}.
        </p>
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={onlyChanges} onChange={(e) => setOnlyChanges(e.target.checked)} /> Changes only
        </label>
      </header>
      {d.games.length === 0 && <Notice>No upcoming games with projections.</Notice>}
      {d.games.map(({ game, players }) => {
        const shown = (onlyChanges ? players.filter((p) => p.changes.length) : players).sort(
          (a, b) => b.changes.length - a.changes.length || (a.line ?? "Z").localeCompare(b.line ?? "Z"),
        );
        return (
          <Panel key={game.id} title={`${game.away} @ ${game.home} · ${longDate(game.date)} ${clock(game.start_time_utc)}`}>
            {shown.length === 0 ? (
              <p className="text-sm text-muted">No deployment changes RinkX can verify.</p>
            ) : (
              <ul className="divide-y divide-line" aria-label={`Deployment ${game.away} @ ${game.home}`}>
                {shown.map((p) => (
                  <Row key={p.id} p={p} text={d.change_text} />
                ))}
              </ul>
            )}
          </Panel>
        );
      })}
    </div>
  );
}
