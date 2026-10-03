import { useState } from "react";
import { Link } from "react-router";
import { odds } from "../components/Lines";
import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { MovementBoard, MovementRow } from "../lib/data/types";
import { clock, localTime } from "../lib/format";

const FIRST_SIDE: Record<MovementRow["kind"], string> = { over_under: "over", yes_no: "yes", moneyline: "home" };

function Row({ r }: { r: MovementRow }) {
  const ch = r.change_pts ?? 0;
  const link = r.player_id ? `/players/${r.player_id}` : `/games/${r.game.id}`;
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-2 py-2 text-sm">
      <span>
        <Link to={link} className="font-semibold hover:text-accent">
          {r.subject}
        </Link>{" "}
        <span className="text-muted">
          {r.market_label}
          {r.line !== null ? ` ${r.line}` : ""} · {r.book_name}
        </span>
        <span className="num block text-xs text-muted">
          {r.game.away} @ {r.game.home} · {clock(r.game.start_time_utc)} · {r.moves} price change
          {r.moves === 1 ? "" : "s"} · last {localTime(r.changed_at)}
        </span>
      </span>
      <span className="num text-right text-xs">
        {FIRST_SIDE[r.kind]} {odds(r.first.over)} → <span className="font-semibold">{odds(r.now.over)}</span>
        <span className={`block ${ch === 0 ? "text-muted" : "text-text"}`}>
          {r.change_pts === null ? "—" : `${ch >= 0 ? "+" : "−"}${Math.abs(ch).toFixed(1)} pts`}
        </span>
      </span>
    </li>
  );
}

export function LineMovement() {
  const res = useEncrypted<MovementBoard>("lines/movement.json");
  const [market, setMarket] = useState("");
  const [movedOnly, setMovedOnly] = useState(true);
  if (res.state === "loading") return <Spinner label="Loading lines…" />;
  if (res.state === "error") return <Notice tone="bad">{res.error.message}</Notice>;
  if (res.state === "unavailable") return <Notice tone="warn">Live data unavailable: no line file published yet.</Notice>;
  const b = res.value.data;
  const markets = Array.from(new Map(b.rows.map((r) => [r.market, r.market_label])).entries());
  const rows = b.rows.filter((r) => (!market || r.market === market) && (!movedOnly || (r.change_pts ?? 0) !== 0));

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">Line movement</h1>
        <p className="text-sm text-muted">
          Every open line for upcoming games, from first seen to now, biggest moves first. The change is in the implied
          probability of the over (or yes, or home) side, margin included. Tap a player for the step chart by book.
        </p>
      </header>
      {b.rows.length === 0 ? (
        <Notice>No open lines for upcoming games. Lines appear once the odds key is set and props are posted.</Notice>
      ) : (
        <Panel
          title={`Lines (${rows.length})`}
          right={
            <div className="flex items-center gap-3 text-xs">
              <label className="flex min-h-9 items-center gap-1.5">
                <input type="checkbox" checked={movedOnly} onChange={(e) => setMovedOnly(e.target.checked)} />
                Moved only
              </label>
              <select
                aria-label="Market"
                value={market}
                onChange={(e) => setMarket(e.target.value)}
                className="min-h-9 rounded border border-line bg-panel px-2"
              >
                <option value="">All markets</option>
                {markets.map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
          }
        >
          {rows.length === 0 ? (
            <p className="text-sm text-muted">No line has moved since it was first seen.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-line" aria-label="Line moves">
              {rows.map((r) => (
                <Row key={`${r.game.id}|${r.subject}|${r.market}|${r.book_name}`} r={r} />
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11px] text-muted">
            Source: {b.source}, as of {localTime(b.generated_at)}. Prices change; check the book before acting.
          </p>
        </Panel>
      )}
    </div>
  );
}
