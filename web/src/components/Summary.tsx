import { odds } from "./Lines";
import type { Summary } from "../lib/backtest";

const pc = (x: number | null) => (x === null ? "—" : `${(x * 100).toFixed(1)}%`);
const u = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}u`;

export function SummaryGrid({ s, label }: { s: Summary; label: string }) {
  const cells: [string, string][] = [
    ["Bets", String(s.bets)],
    ["W–L–P", `${s.wins}–${s.losses}–${s.pushes}`],
    ["Win %", pc(s.win_pct)],
    ["Units", u(s.units)],
    ["ROI", pc(s.roi)],
    ["Avg odds", s.avg_odds === null ? "—" : odds(s.avg_odds)],
    ["CLV (avg)", s.clv_cents === null ? "—" : `${s.clv_cents >= 0 ? "+" : "−"}${Math.abs(s.clv_cents).toFixed(1)}¢`],
    ["Beat the close", pc(s.beat_close)],
    ["Max drawdown", `${s.drawdown.toFixed(2)}u`],
  ];
  return (
    <dl className="num grid grid-cols-3 gap-2 text-sm sm:grid-cols-5" aria-label={label}>
      {cells.map(([k, v]) => (
        <div key={k}>
          <dt className="text-[11px] text-muted">{k}</dt>
          <dd className="font-semibold">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
