import { useMemo, useState } from "react";
import { odds } from "../components/Lines";
import { SummaryGrid } from "../components/Summary";
import { Notice, Panel, Spinner } from "../components/ui";
import { type BacktestFile, parse, summarize } from "../lib/backtest";
import { useEncrypted } from "../lib/data/fetch";
import { gradedFor, MARKET_GROUPS, myBets, useMyBets } from "../lib/myBets";

type Range = "7" | "30" | "season";

export function MyPerformance() {
  const res = useEncrypted<BacktestFile>("backtest.json");
  const all = useMyBets();
  const [range, setRange] = useState<Range>("30");
  const rows = useMemo(() => (res.state === "ready" ? parse(res.value.data) : []), [res]);
  if (res.state === "loading") return <Spinner label="Loading results…" />;
  const since = range === "season" ? "" : new Date(Date.now() - Number(range) * 864e5).toISOString().slice(0, 10);
  const mine = all.filter((b) => !since || b.placed_at.slice(0, 10) >= since); // by when it was placed
  const graded = mine
    .map((b) => ({ b, r: gradedFor(b, rows) }))
    .filter((x): x is { b: (typeof mine)[number]; r: NonNullable<ReturnType<typeof gradedFor>> } => x.r !== null);
  const pending = mine.length - graded.length;
  const s = summarize(graded.map(({ b, r }) => ({ r, price: b.price, stake: b.stake })));
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">My performance</h1>
        <p className="text-sm text-muted">
          Bets you track from a prop card ("Track this bet"), kept on this device only. Results and closing prices come
          from RinkX's grading; units use your price and stake.
        </p>
      </header>
      <div className="flex gap-1.5" role="tablist" aria-label="Range">
        {(["7", "30", "season"] as Range[]).map((r) => (
          <button key={r} type="button" role="tab" aria-selected={range === r} onClick={() => setRange(r)}
            className={`min-h-9 rounded-full border px-3 text-xs ${range === r ? "border-accent bg-accent/15" : "border-line text-muted"}`}>
            {r === "season" ? "Season" : `${r} days`}
          </button>
        ))}
      </div>
      {all.length === 0 ? (
        <Notice>No tracked bets yet. Open a prop and tap “Track this bet”.</Notice>
      ) : (
        <>
          <Panel title={`Results (${graded.length} graded${pending ? `, ${pending} pending` : ""})`}>
            {graded.length ? <SummaryGrid s={s} label="My results" /> : <p className="text-sm text-muted">Nothing graded in this range yet.</p>}
          </Panel>
          <Panel title="By market">
            <table className="num w-full text-xs" aria-label="My results by market">
              <thead className="text-muted">
                <tr>
                  <th className="text-left font-normal">Market</th>
                  <th className="text-right font-normal">Bets</th>
                  <th className="text-right font-normal">W–L–P</th>
                  <th className="text-right font-normal">Units</th>
                  <th className="text-right font-normal">ROI</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(MARKET_GROUPS).map(([name, codes]) => {
                  const g = graded.filter((x) => codes.includes(x.b.market));
                  const t = summarize(g.map(({ b, r }) => ({ r, price: b.price, stake: b.stake })));
                  return (
                    <tr key={name}>
                      <td>{name}</td>
                      <td className="text-right">{t.bets}</td>
                      <td className="text-right">
                        {t.wins}–{t.losses}–{t.pushes}
                      </td>
                      <td className="text-right">{t.bets ? t.units.toFixed(2) : "—"}</td>
                      <td className="text-right">{t.roi === null ? "—" : `${(t.roi * 100).toFixed(1)}%`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
          <Panel title="Tracked bets">
            <ul className="flex flex-col divide-y divide-line text-sm" aria-label="Tracked bets">
              {[...mine].reverse().map((b) => {
                const r = gradedFor(b, rows);
                return (
                  <li key={b.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
                    <span>
                      {b.player} {b.side} {b.line ?? ""} {b.label} · {b.book} · {b.date}
                    </span>
                    <span className="num flex items-center gap-2 text-xs">
                      <label className="flex items-center gap-1">
                        price
                        <input className="w-16 rounded border border-line bg-panel px-1" inputMode="numeric" defaultValue={b.price}
                          onBlur={(e) => {
                            const v = Number(e.target.value);
                            if (Math.abs(v) >= 100) myBets.update(b.id, { price: v });
                          }} />
                      </label>
                      <label className="flex items-center gap-1">
                        units
                        <input className="w-12 rounded border border-line bg-panel px-1" inputMode="decimal" defaultValue={b.stake}
                          onBlur={(e) => {
                            const v = Number(e.target.value);
                            if (v > 0) myBets.update(b.id, { stake: v });
                          }} />
                      </label>
                      <span>{r ? `${r.result}${r.close !== null ? ` · close ${odds(r.close)}` : ""}` : "pending"}</span>
                      <button type="button" className="text-muted underline" onClick={() => myBets.remove(b.id)}>
                        remove
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </Panel>
        </>
      )}
    </div>
  );
}
