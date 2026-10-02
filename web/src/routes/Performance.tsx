import { american } from "../components/Lines";
import { DataChip, Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { BetRecord, GradedBet, Performance as PerformanceData } from "../lib/data/types";
import { localTime, longDate } from "../lib/format";

const LINE = "#3987e5";
const SIDE_LABEL: Record<string, string> = { over: "Over", under: "Under", yes: "Yes", no: "No", home: "Home", away: "Away" };

const units = (u: number) => `${u >= 0 ? "+" : "−"}${Math.abs(u).toFixed(2)}u`;
const pctSigned = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(digits)}%`;

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-md border border-line bg-panel-2 p-3">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="num text-lg font-semibold">{value}</div>
      {sub && <div className="num text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

function ProfitChart({ series, drawdown }: { series: PerformanceData["series"]; drawdown: number }) {
  if (series.length < 2) return <p className="text-xs text-muted">Needs at least two days of graded bets.</p>;
  const W = 600;
  const H = 160;
  const ys = series.map((d) => d.cumulative);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const span = hi - lo || 1;
  const x = (i: number) => 40 + (i / (series.length - 1)) * (W - 50);
  const y = (v: number) => 10 + (1 - (v - lo) / span) * (H - 30);
  const d = series.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cumulative).toFixed(1)}`).join("");
  const last = series[series.length - 1]!;
  return (
    <figure className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Cumulative profit">
        <line x1={40} x2={W - 10} y1={y(0)} y2={y(0)} stroke="currentColor" strokeOpacity={0.3} strokeDasharray="4 4" />
        <text x={34} y={y(0) + 3} textAnchor="end" className="fill-muted text-[10px]">
          0
        </text>
        <text x={34} y={y(hi) + 3} textAnchor="end" className="fill-muted text-[10px]">
          {hi.toFixed(1)}
        </text>
        {lo < 0 && (
          <text x={34} y={y(lo) + 3} textAnchor="end" className="fill-muted text-[10px]">
            {lo.toFixed(1)}
          </text>
        )}
        <path d={d} fill="none" stroke={LINE} strokeWidth={2} strokeLinejoin="round" />
        {series.map((p, i) => (
          <circle key={p.date} cx={x(i)} cy={y(p.cumulative)} r={3} fill={LINE}>
            <title>{`${longDate(p.date)}: ${units(p.profit)} on ${p.bets} bets, total ${units(p.cumulative)}`}</title>
          </circle>
        ))}
        <text x={40} y={H - 4} className="fill-muted text-[10px]">
          {longDate(series[0]!.date)}
        </text>
        <text x={W - 10} y={H - 4} textAnchor="end" className="fill-muted text-[10px]">
          {longDate(last.date)}
        </text>
      </svg>
      <figcaption className="num text-[11px] text-muted">
        Units won or lost, 1 unit per bet. Worst drawdown from a high point: {units(drawdown)}.
      </figcaption>
    </figure>
  );
}

function Reliability({ bins }: { bins: NonNullable<PerformanceData["calibration"]["reliability"]> }) {
  const S = 200;
  const pad = 24;
  const sc = (v: number) => pad + v * (S - pad - 6);
  const maxN = Math.max(...bins.map((b) => b.n), 1);
  return (
    <svg viewBox={`0 0 ${S} ${S}`} className="w-full max-w-[260px]" role="img" aria-label="Reliability diagram">
      <rect x={pad} y={6} width={S - pad - 6} height={S - pad - 6} fill="none" stroke="currentColor" strokeOpacity={0.15} />
      <line x1={sc(0)} y1={S - sc(0)} x2={sc(1)} y2={S - sc(1)} stroke="currentColor" strokeOpacity={0.35} strokeDasharray="4 4" />
      {bins.map((b) => (
        <circle
          key={b.lo}
          cx={sc(b.mean_p)}
          cy={S - sc(b.hit_rate)}
          r={2.5 + 5 * Math.sqrt(b.n / maxN)}
          fill={LINE}
          fillOpacity={0.75}
        >
          <title>{`Said ${(b.mean_p * 100).toFixed(0)}%, happened ${(b.hit_rate * 100).toFixed(0)}% (${b.n} props)`}</title>
        </circle>
      ))}
      <text x={S / 2 + pad / 2} y={S - 4} textAnchor="middle" className="fill-muted text-[9px]">
        model probability
      </text>
      <text x={8} y={S / 2} textAnchor="middle" transform={`rotate(-90 8 ${S / 2})`} className="fill-muted text-[9px]">
        how often it happened
      </text>
    </svg>
  );
}

function SplitTable({ title, rows, note }: { title: string; rows: (BetRecord & { key: string })[]; note?: string }) {
  if (rows.length === 0) return null;
  return (
    <Panel title={title}>
      <div className="overflow-x-auto">
        <table className="num w-full text-sm" aria-label={title}>
          <thead className="text-left text-[11px] text-muted">
            <tr>
              <th className="py-1 pr-2 font-normal" />
              <th className="py-1 pr-2 text-right font-normal">Bets</th>
              <th className="py-1 pr-2 text-right font-normal">W-L-P</th>
              <th className="py-1 pr-2 text-right font-normal">Profit</th>
              <th className="py-1 pr-2 text-right font-normal">ROI</th>
              <th className="py-1 text-right font-normal">CLV</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-line">
                <td className="whitespace-nowrap py-1 pr-2 font-sans">{r.key}</td>
                <td className="py-1 pr-2 text-right">{r.n}</td>
                <td className="py-1 pr-2 text-right">
                  {r.wins}-{r.losses}-{r.pushes}
                </td>
                <td className={`py-1 pr-2 text-right ${r.profit >= 0 ? "text-over" : "text-bad"}`}>{units(r.profit)}</td>
                <td className="py-1 pr-2 text-right">{pctSigned(r.roi)}</td>
                <td className="py-1 text-right">{pctSigned(r.avg_clv)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <p className="mt-2 text-[11px] text-muted">{note}</p>}
    </Panel>
  );
}

const OUTCOME_STYLE: Record<GradedBet["outcome"], string> = {
  win: "border-over/40 text-over",
  loss: "border-bad/40 text-bad",
  push: "border-line text-muted",
  void: "border-line text-muted",
};

function Recent({ rows }: { rows: GradedBet[] }) {
  return (
    <Panel title="Recent graded bets">
      <ul className="flex flex-col divide-y divide-line" aria-label="Recent graded bets">
        {rows.map((r) => (
          <li key={r.prediction_id} className="flex flex-wrap items-baseline justify-between gap-2 py-1.5 text-sm">
            <span>
              <span className="font-semibold">{r.subject}</span>{" "}
              <span className="text-muted">
                {SIDE_LABEL[r.side] ?? r.side}
                {r.line !== null ? ` ${r.line}` : ""} {r.market_label}
              </span>{" "}
              <span className="num">{american(r.price)}</span>
            </span>
            <span className="num flex items-center gap-2 text-xs text-muted">
              {longDate(r.date)} · {r.book_name}
              {r.actual !== null && ` · actual ${r.actual}`}
              {r.clv !== null && ` · CLV ${pctSigned(r.clv)}`}
              <span className={`rounded border px-1 text-[10px] font-semibold uppercase ${OUTCOME_STYLE[r.outcome]}`}>
                {r.outcome === "void" ? `void: ${r.void_reason ?? ""}` : r.outcome}
              </span>
              {r.profit !== null && r.outcome !== "void" && <span>{units(r.profit)}</span>}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

export function Performance() {
  const res = useEncrypted<PerformanceData>("performance.json");
  if (res.state === "loading") return <Spinner label="Loading performance…" />;
  if (res.state === "error") return <Notice tone="bad">{res.error.message}</Notice>;
  if (res.state === "unavailable") return <Notice tone="warn">Live data unavailable: no performance file published yet.</Notice>;
  const p = res.value.data;
  const b = p.bets;
  const cal = p.calibration;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-lg font-semibold">Model performance</h1>
          {p.synthetic && <DataChip state="synthetic" detail="test data" />}
        </div>
        <p className="text-sm text-muted">
          How RinkX's frozen predictions did once the games were played. Live-tracked: each one was priced before the
          game, with what was known then, and graded by book rules (stats include overtime, never the shootout; a
          player who doesn't play voids the bet). One bet per prop, at the best price RinkX saw, 1 unit each. Losing
          stretches stay in. The walk-forward tests on{" "}
          <a href="#/models" className="text-accent underline">
            Model Tests
          </a>{" "}
          are the other view.
        </p>
        <p className="num text-xs text-muted">
          {p.period ? `${longDate(p.period.from)} – ${longDate(p.period.to)} · ` : ""}updated {localTime(p.generated_at)}
        </p>
      </header>

      {p.graded === 0 ? (
        <Notice>
          Nothing graded yet. Each prediction is graded once its game is final and the box score is in, so results
          start the day after the first priced game.
        </Notice>
      ) : (
        <>
          {b.n < p.min_bets && (
            <Notice tone="warn">
              Only {b.n} graded bets. That is far too few to tell skill from luck: at this size the ROI could easily be
              anywhere from {pctSigned(b.roi_ci?.[0])} to {pctSigned(b.roi_ci?.[1])}.
            </Notice>
          )}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Summary">
            <Tile label="Bets (W-L-P)" value={String(b.n)} sub={`${b.wins}-${b.losses}-${b.pushes}`} />
            <Tile label="Profit" value={units(b.profit)} sub={`expected ${pctSigned(b.expected_roi)} per bet`} />
            <Tile
              label="ROI (95% range)"
              value={pctSigned(b.roi)}
              sub={b.roi_ci ? `${pctSigned(b.roi_ci[0])} to ${pctSigned(b.roi_ci[1])}` : undefined}
            />
            <Tile
              label="Closing-line value"
              value={pctSigned(b.avg_clv)}
              sub={b.beat_close !== null ? `beat the close ${(b.beat_close * 100).toFixed(0)}% of the time` : "no closing prices"}
            />
          </div>

          <Panel title="Profit over time">
            <ProfitChart series={p.series} drawdown={p.max_drawdown} />
          </Panel>

          <Panel title="Calibration">
            <div className="flex flex-col gap-4 sm:flex-row">
              {cal.reliability && cal.reliability.length > 0 && <Reliability bins={cal.reliability} />}
              <div className="flex flex-col gap-2 text-sm">
                <p className="text-xs text-muted">
                  Every graded prop counts here, with or without a lean ({cal.n.toLocaleString()} props). Dots on the
                  dashed line mean the probabilities came true as often as they said. Lower Brier and log loss are
                  better.
                </p>
                <dl className="num grid grid-cols-3 gap-x-4 gap-y-1 text-sm">
                  <dt className="text-xs text-muted" />
                  <dt className="text-xs text-muted">Brier</dt>
                  <dt className="text-xs text-muted">Log loss</dt>
                  {cal.model_same_rows && cal.market && (
                    <>
                      <dd className="text-xs text-muted">RinkX</dd>
                      <dd>{cal.model_same_rows.brier.toFixed(4)}</dd>
                      <dd>{cal.model_same_rows.log_loss.toFixed(4)}</dd>
                      <dd className="text-xs text-muted">No-vig market</dd>
                      <dd>{cal.market.brier.toFixed(4)}</dd>
                      <dd>{cal.market.log_loss.toFixed(4)}</dd>
                    </>
                  )}
                </dl>
                {cal.model_same_rows && cal.market && (
                  <p className="text-xs text-muted">
                    {cal.model_same_rows.brier < cal.market.brier
                      ? "On these props the model's probabilities scored better than the market's."
                      : "On these props the market's probabilities scored at least as well as the model's."}{" "}
                    ({cal.n_vs_market} props with both sides priced.)
                  </p>
                )}
                {cal.ece !== undefined && cal.ece !== null && (
                  <p className="num text-xs text-muted">
                    Average calibration gap (ECE): {(cal.ece * 100).toFixed(1)} pts.
                  </p>
                )}
              </div>
            </div>
          </Panel>

          <SplitTable title="By market" rows={p.by_market} />
          <SplitTable
            title="By confidence"
            rows={p.by_confidence}
            note={
              p.confidence_monotonic === null
                ? "Too few bets per confidence bucket to check whether higher confidence returns more."
                : p.confidence_monotonic
                  ? "Higher-confidence buckets have returned at least as much per bet as lower ones."
                  : "Higher confidence has not meant a higher return per bet so far: the confidence weights need another look."
            }
          />
          <SplitTable title="By month" rows={p.by_month} />
          <SplitTable title="By model version" rows={p.by_version} />
          {Object.keys(p.voids).length > 0 && (
            <p className="text-xs text-muted">
              Voided (stake returned):{" "}
              {Object.entries(p.voids)
                .map(([k, n]) => `${k} ${n}`)
                .join(" · ")}
            </p>
          )}
          {p.recent.length > 0 && <Recent rows={p.recent} />}
          <p className="text-[11px] text-muted">Past results don't predict future ones. Statistical estimates, not guarantees.</p>
        </>
      )}
    </div>
  );
}
