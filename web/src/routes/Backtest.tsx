import { useMemo, useState } from "react";
import { SummaryGrid } from "../components/Summary";
import { Freshness } from "../components/Freshness";
import { Notice, Panel, Spinner } from "../components/ui";
import { type BacktestFile, BT_DEFAULT, type BtFilter, bets, clvBy, type GroupBy, parse, summarize } from "../lib/backtest";
import { useEncrypted } from "../lib/data/fetch";
import { longDate } from "../lib/format";

const sel = "min-h-9 rounded border border-line bg-panel px-2 text-sm text-text";

export function Backtest() {
  const res = useEncrypted<BacktestFile>("backtest.json");
  const [f, setF] = useState<BtFilter>(BT_DEFAULT);
  const [by, setBy] = useState<GroupBy>("market");
  const rows = useMemo(() => (res.state === "ready" ? parse(res.value.data) : []), [res]);
  const picked = useMemo(() => bets(rows, f), [rows, f]);
  const s = useMemo(() => summarize(picked.map((r) => ({ r }))), [picked]);
  if (res.state === "loading") return <Spinner label="Loading graded props…" />;
  if (res.state !== "ready") return <Notice tone="warn">DATA UNAVAILABLE: nothing graded yet.</Notice>;
  const set = (p: Partial<BtFilter>) => setF({ ...f, ...p });
  const uniq = (xs: (string | null)[]) => Array.from(new Set(xs.filter((x): x is string => !!x))).sort();
  const markets = Array.from(new Map(rows.map((r) => [r.market, r.label])));
  const synthetic = rows.some((r) => r.synthetic);
  const clv = clvBy(rows.filter((r) => r.lean), by);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">Backtest</h1>
        <p className="text-sm text-muted">
          Replays RinkX's own pre-game prices since {longDate(res.value.data.since)}: for each prop and book, the last
          price frozen before puck drop. Filters use only those frozen values (model probability, edge, Prop
          Intelligence, line, book), so nothing looks ahead; one bet per prop at the best price that passes, 1 unit
          each.
        </p>
        <Freshness at={res.value.meta.generated_at} staleAfterMin={180} source="graded by the RinkX pipeline" />
      </header>
      {synthetic && <Notice tone="warn">SYNTHETIC test data: these results are not real.</Notice>}
      <Panel title="Filters">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="group" aria-label="Backtest filters">
          <label className="flex flex-col text-[11px] text-muted">
            Market
            <select className={sel} value={f.market} onChange={(e) => set({ market: e.target.value })}>
              <option value="">All</option>
              {markets.map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-muted">
            Line
            <input className={sel} inputMode="decimal" value={f.line} placeholder="e.g. 2.5" onChange={(e) => set({ line: e.target.value })} />
          </label>
          <label className="flex flex-col text-[11px] text-muted">
            Min model probability
            <select className={sel} value={f.minProb} onChange={(e) => set({ minProb: Number(e.target.value) })}>
              {[0, 50, 55, 60, 65, 70].map((v) => (
                <option key={v} value={v}>
                  {v ? `${v}%` : "Any"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-muted">
            Min edge
            <select className={sel} value={f.minEdge} onChange={(e) => set({ minEdge: Number(e.target.value) })}>
              {[0, 3, 5, 8, 10].map((v) => (
                <option key={v} value={v}>
                  {v ? `${v}%` : "Any"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-muted">
            Min Prop Intelligence
            <select className={sel} value={f.minScore} onChange={(e) => set({ minScore: Number(e.target.value) })}>
              {[0, 60, 70, 80, 90].map((v) => (
                <option key={v} value={v}>
                  {v || "Any"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-muted">
            Book
            <select className={sel} value={f.book} onChange={(e) => set({ book: e.target.value })}>
              <option value="">Best available</option>
              {uniq(rows.map((r) => r.book)).map((b) => (
                <option key={b}>{b}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-muted">
            Side
            <select className={sel} value={f.side} onChange={(e) => set({ side: e.target.value })}>
              <option value="">Any</option>
              {["over", "under", "yes", "no", "home", "away"].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label className="flex min-h-9 items-center gap-2 self-end text-sm">
            <input type="checkbox" checked={f.leansOnly} onChange={(e) => set({ leansOnly: e.target.checked })} /> RinkX leans only
          </label>
        </div>
      </Panel>
      <Panel title={`Results (${s.bets} bets)`}>
        {s.bets === 0 ? (
          <p className="text-sm text-muted">INSUFFICIENT DATA: no graded prop matches these filters.</p>
        ) : (
          <SummaryGrid s={s} label="Backtest results" />
        )}
        {s.bets > 0 && s.bets < 100 && (
          <p className="mt-2 text-xs text-warn">Fewer than 100 bets: far too few to tell skill from luck.</p>
        )}
      </Panel>
      <Panel
        title="Closing-line value"
        right={
          <select className={sel} value={by} onChange={(e) => setBy(e.target.value as GroupBy)} aria-label="Group CLV by">
            <option value="market">By market</option>
            <option value="player">By player</option>
            <option value="book">By sportsbook</option>
            <option value="version">By model version</option>
          </select>
        }
      >
        <p className="mb-2 text-xs text-muted">
          Every graded lean: the price taken vs the closing price at that book (cents; positive = beat the close) and
          the value of the bet at the closing no-vig probability. Groups with 5+ bets.
        </p>
        {clv.length === 0 ? (
          <p className="text-sm text-muted">INSUFFICIENT DATA: no closing prices recorded yet.</p>
        ) : (
          <table className="num w-full text-xs" aria-label="CLV breakdown">
            <thead className="text-muted">
              <tr>
                <th className="text-left font-normal">Group</th>
                <th className="text-right font-normal">Bets</th>
                <th className="text-right font-normal">CLV</th>
                <th className="text-right font-normal">Value at close</th>
              </tr>
            </thead>
            <tbody>
              {clv.slice(0, 25).map((g) => (
                <tr key={g.key}>
                  <td>{g.key}</td>
                  <td className="text-right">{g.n}</td>
                  <td className="text-right">
                    {g.clv_cents >= 0 ? "+" : "−"}
                    {Math.abs(g.clv_cents).toFixed(1)}¢
                  </td>
                  <td className="text-right">{g.clv_ev === null ? "—" : `${(g.clv_ev * 100).toFixed(1)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
