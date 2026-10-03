import { useMemo, useState } from "react";
import { Link } from "react-router";
import { FeedsPanel } from "../components/Feeds";
import { Freshness } from "../components/Freshness";
import { PublicBetting } from "../components/Scores";
import { cents, odds } from "../components/Lines";
import { GROUPS, PropGroups, PropLine, bestPerProp } from "../components/PropGroups";
import { GoalieLine } from "../components/Projections";
import { Notice, Panel, Spinner } from "../components/ui";
import { Drawer } from "./BestProps";
import { useEncrypted, useManifest } from "../lib/data/fetch";
import type { AlertsHistory, BestProps, MovementBoard, NewsFeed, PropRow, Slate } from "../lib/data/types";
import { clock, localTime, longDate } from "../lib/format";
import { buildReport, reportText } from "../lib/report";
import { projEdge } from "../lib/propFilters";

const STALE_MIN = 120;
const SIDE: Record<string, string> = { over: "Over", under: "Under", yes: "Yes", no: "No" };


export function Today() {
  const manifest = useManifest().data;
  const date = manifest?.slate_date ?? "";
  const props = useEncrypted<BestProps>("props/best.json");
  const moves = useEncrypted<MovementBoard>("lines/movement.json");
  const news = useEncrypted<NewsFeed>("news.json");
  const alerts = useEncrypted<AlertsHistory>("alerts.json");
  const slate = useEncrypted<Slate>(`slate/${date}.json`);
  const [market, setMarket] = useState("");
  const [open, setOpen] = useState<PropRow | null>(null);
  const [copied, setCopied] = useState(false);

  const rows = useMemo(
    () => (props.state === "ready" ? props.value.data.rows.filter((r) => r.game.date === date) : []),
    [props, date],
  );
  if (!manifest || props.state === "loading") return <Spinner label="Loading today…" />;
  const title = <h1 className="text-lg font-semibold">Today's slate · {date ? longDate(date) : "—"}</h1>;
  if (!manifest.feeds.some((f) => f.state !== "unavailable")) {
    return (
      <div className="mx-auto flex max-w-4xl flex-col gap-4">
        {title}
        <Notice tone="warn">
          Live data unavailable. No data sources are connected yet, so there is no slate, no projections and no lines
          to show.
        </Notice>
        <FeedsPanel feeds={manifest.feeds} generatedAt={manifest.generated_at} />
      </div>
    );
  }
  const games = slate.state === "ready" ? slate.value.data.games : [];
  const marketRows = market ? rows.filter((r) => GROUPS.find((g) => g.key === market)!.markets.includes(r.market)) : rows;
  const leans = bestPerProp(marketRows.filter((r) => r.lean));
  const top10 = [...leans].sort((a, b) => (b.scores?.intelligence ?? -1) - (a.scores?.intelligence ?? -1)).slice(0, 10);
  // one row per player/market/line, on the side the projection favours; only positive gaps
  const edgeBy = new Map<string, PropRow>();
  for (const r of bestPerProp(marketRows)) {
    const e = projEdge(r);
    if (e === null || e <= 0) continue;
    const k = `${r.subject.name}|${r.market}|${r.line}`;
    if (!edgeBy.has(k) || projEdge(edgeBy.get(k)!)! < e) edgeBy.set(k, r);
  }
  const edges = [...edgeBy.values()].sort((a, b) => projEdge(b)! - projEdge(a)!).slice(0, 5);
  const moveRows = moves.state === "ready" ? moves.value.data.rows.filter((m) => m.game.start_time_utc.slice(0, 10) >= date) : [];
  const bigMoves = [...moveRows].filter((m) => m.change_pts !== null).sort((a, b) => Math.abs(b.change_pts!) - Math.abs(a.change_pts!)).slice(0, 5);
  const newsItems = news.state === "ready" ? news.value.data.items : [];
  const alertEvents = alerts.state === "ready" ? alerts.value.data.events.slice(0, 6) : [];
  const generated = props.state === "ready" ? props.value.meta.generated_at : null;
  const stale = generated ? (Date.now() - Date.parse(generated)) / 60000 > STALE_MIN : true;
  const report = buildReport({ props: rows, moves: moveRows, news: newsItems, games, stale });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header className="flex flex-col gap-1">
        {title}
        <Freshness at={generated} staleAfterMin={STALE_MIN} source="RinkX pipeline" />
        <label className="flex items-center gap-2 self-start text-[11px] text-muted">
          Market
          <select value={market} onChange={(e) => setMarket(e.target.value)} className="min-h-9 rounded border border-line bg-panel px-2 text-sm text-text">
            <option value="">All markets</option>
            {GROUPS.map((g) => (
              <option key={g.key} value={g.key}>
                {g.title.replace("🔥 Best ", "").replace(" props", "")}
              </option>
            ))}
          </select>
        </label>
      </header>
      {props.state !== "ready" && <Notice tone="warn">DATA UNAVAILABLE: no props file published yet.</Notice>}

      <Panel title={`🔥 Top 10 props (${games.length} games)`}>
        {top10.length === 0 ? (
          <p className="text-sm text-muted">No lean clears the bar today. That's normal: most lines are efficient.</p>
        ) : (
          <ol className="divide-y divide-line" aria-label="Top 10 props">
            {top10.map((r) => (
              <PropLine key={r.prediction_id} r={r} onOpen={() => setOpen(r)} />
            ))}
          </ol>
        )}
      </Panel>

      <Panel title="By market">
        <PropGroups rows={marketRows.filter((r) => r.lean)} label="Today by market" />
      </Panel>

      <div className="grid gap-4 sm:grid-cols-2">
        <Panel title="🔥 Biggest projection edges">
          {edges.length === 0 ? (
            <p className="text-sm text-muted">INSUFFICIENT DATA: no projection clears a priced line today.</p>
          ) : (
            <ul className="divide-y divide-line text-sm" aria-label="Projection edges">
              {edges.map((r) => (
                <li key={r.prediction_id} className="flex justify-between gap-2 py-1">
                  <button type="button" className="text-left hover:text-accent" onClick={() => setOpen(r)}>
                    {r.subject.name} {SIDE[r.lean ?? r.side_scored] ?? ""} {r.line} {r.market_label}
                  </button>
                  <span className="num">
                    proj {r.projection?.toFixed(2)} ({projEdge(r)! >= 0 ? "+" : "−"}
                    {Math.abs(projEdge(r)!).toFixed(2)})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="📈 Biggest line moves">
          {bigMoves.length === 0 ? (
            <p className="text-sm text-muted">No line has moved since it opened.</p>
          ) : (
            <ul className="divide-y divide-line text-sm" aria-label="Biggest line moves">
              {bigMoves.map((m) => (
                <li key={`${m.subject}-${m.market}-${m.book_name}`} className="flex flex-wrap justify-between gap-2 py-1">
                  <span>
                    {m.subject} {m.line ?? ""} {m.market_label} · {m.book_name}
                  </span>
                  <span className="num text-xs">
                    {odds(m.first.over)} → {odds(m.now.over)} ({Math.abs(cents(m.first.over!, m.now.over!))}¢)
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="⚠️ Injury and lineup alerts">
          {newsItems.length === 0 && alertEvents.length === 0 ? (
            <p className="text-sm text-muted">Nothing entered or triggered yet today.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm" aria-label="Lineup alerts">
              {alertEvents.map((e) => (
                <li key={`${e.key}-${e.triggered_at}`}>
                  {e.message} <span className="text-xs text-muted">· {localTime(e.triggered_at)}</span>
                </li>
              ))}
              {newsItems.slice(0, 6).map((n) => (
                <li key={n.id}>
                  {n.headline}{" "}
                  <a href={n.url} target="_blank" rel="noreferrer" className="text-xs text-accent underline">
                    source
                  </a>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="🥅 Starting goalies">
          {games.length === 0 ? (
            <p className="text-sm text-muted">No games today.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm" aria-label="Starting goalies">
              {games.map((g) => (
                <li key={g.id}>
                  <Link to={`/games/${g.id}`} className="num text-xs text-muted">
                    {g.away.team.abbrev} @ {g.home.team.abbrev} · {clock(g.start_time_utc)}
                  </Link>
                  {[g.away, g.home].map((s) => (
                    <div key={s.team.abbrev} className="flex items-center gap-2">
                      <span className="num w-10 text-xs text-muted">{s.team.abbrev}</span>
                      <GoalieLine g={s.goalie} gameId={g.id} team={s.team.abbrev} />
                    </div>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel
        title="Daily report"
        right={
          <button
            type="button"
            className="min-h-9 text-xs text-accent underline"
            onClick={() => {
              void navigator.clipboard?.writeText(reportText(date, report)).then(() => setCopied(true));
            }}
          >
            {copied ? "Copied" : "Copy text"}
          </button>
        }
      >
        <div className="flex flex-col gap-3" aria-label="Daily report">
          {report.map((s) => (
            <section key={s.title}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{s.title}</h3>
              <ul className="mt-0.5 flex flex-col gap-0.5 text-sm">
                {s.lines.length ? s.lines.map((l) => <li key={l}>• {l}</li>) : <li className="text-muted">• {s.empty}</li>}
              </ul>
            </section>
          ))}
          <p className="text-[11px] text-muted">
            Each line restates a published number (prices, model and market probabilities, scores, movement, entries
            with their sources). Statistical estimates, not certainties.
          </p>
          <PublicBetting />
        </div>
      </Panel>
      <FeedsPanel feeds={manifest.feeds} generatedAt={manifest.generated_at} only={(f) => f.state === "failed" || f.state === "stale"} />
      {open && <Drawer r={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
