import type { BookLine, GameLines, Lean, LineMarket, LineRow, Pricing } from "../lib/data/types";
import { localTime } from "../lib/format";
import { pct } from "./Projections";

/** Book series colors: categorical slots 1-2 of the validated palette, dark-surface steps. */
const BOOK_COLORS = ["#3987e5", "#d95926"];

export function american(p: number | null | undefined): string {
  if (p === null || p === undefined) return "—";
  return p > 0 ? `+${p}` : `−${Math.abs(p)}`;
}

function implied(p: number): number {
  return p > 0 ? 100 / (p + 100) : -p / (-p + 100);
}

function sides(kind: LineMarket["kind"]): [string, string] {
  if (kind === "yes_no") return ["Yes", "No"];
  if (kind === "moneyline") return ["Home", "Away"];
  return ["Over", "Under"];
}

function Price({ value, best }: { value: number | null; best: boolean }) {
  return (
    <span className={best ? "font-semibold text-accent" : ""} title={best ? "Best price" : undefined}>
      {american(value)}
      {best && <span className="sr-only"> (best)</span>}
    </span>
  );
}

export const SIDE_LABEL: Record<string, string> = {
  over: "Over",
  under: "Under",
  yes: "Yes",
  no: "No",
  home: "Home",
  away: "Away",
};

export function pts(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  const v = x * 100;
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}`;
}

function LeanText({ lean }: { lean: Lean | null }) {
  if (!lean) return <span className="text-xs text-muted">No lean</span>;
  return (
    <span className="whitespace-nowrap text-xs">
      <span className="font-semibold">{SIDE_LABEL[lean.side]}</span> {american(lean.price)}{" "}
      <span className="text-muted">({lean.book_name})</span>
      <br />
      <span className="text-muted">
        edge {pts(lean.edge)} pts · EV {lean.ev >= 0 ? "+" : "−"}
        {Math.abs(lean.ev).toFixed(2)}u · conf {lean.confidence ?? "—"}
      </span>
    </span>
  );
}

function subject(r: LineRow): string {
  return r.player?.name ?? r.team ?? "Game";
}

function MarketTable({ m, books }: { m: LineMarket; books: string[] }) {
  const [a, b] = sides(m.kind);
  const bookCodes = Array.from(new Set(m.rows.flatMap((r) => r.books.map((x) => x.book))));
  const names = Object.fromEntries(m.rows.flatMap((r) => r.books.map((x) => [x.book, x.book_name])));
  return (
    <div className="overflow-x-auto">
      <table className="num w-full min-w-[480px] text-sm" aria-label={`${m.label} lines`}>
        <thead className="text-xs text-muted">
          <tr>
            <th className="py-1 text-left font-normal">{m.kind === "moneyline" ? "Home team" : "Player"}</th>
            {m.kind === "over_under" && <th className="py-1 text-right font-normal">Line</th>}
            {bookCodes.map((c) => (
              <th key={c} className="py-1 text-right font-normal">
                {names[c] ?? c}
                <div className="text-[10px]">
                  {a} / {b}
                </div>
              </th>
            ))}
            <th className="py-1 text-right font-normal">
              No-vig {a.toLowerCase()}
              <div className="text-[10px]">consensus</div>
            </th>
            <th className="py-1 text-right font-normal">
              Model {a.toLowerCase()}
              <div className="text-[10px]">at the line</div>
            </th>
            <th className="py-1 pl-3 text-left font-normal">Lean</th>
          </tr>
        </thead>
        <tbody>
          {m.rows.map((r) => (
            <tr key={subject(r)} className="border-t border-line">
              <td className="py-1.5 pr-2 font-sans">{subject(r)}</td>
              {m.kind === "over_under" && (
                <td className="text-right">
                  {r.line ?? "—"}
                  {r.lines_differ && (
                    <span className="ml-1 text-[10px] text-warn" title="Books differ on the line">
                      ≠
                    </span>
                  )}
                </td>
              )}
              {bookCodes.map((c) => {
                const x: BookLine | undefined = r.books.find((y) => y.book === c);
                if (!x) return <td key={c} className="text-right text-muted">—</td>;
                const sameLine = x.line === r.line;
                return (
                  <td key={c} className="text-right">
                    {m.kind === "over_under" && !sameLine && <span className="text-[10px] text-muted">{x.line} </span>}
                    <Price value={x.over} best={sameLine && r.best_over?.book === c && bookCodes.length > 1} />
                    {" / "}
                    <Price value={x.under} best={sameLine && r.best_under?.book === c && bookCodes.length > 1} />
                  </td>
                );
              })}
              <td className="text-right">
                {r.consensus ? (
                  pct(r.consensus.p_over)
                ) : (
                  <span className="text-[11px] italic text-muted" title="Only one side is offered, so the margin can't be removed">
                    one-sided
                  </span>
                )}
              </td>
              <td className="text-right">{modelPct(r)}</td>
              <td className="py-1.5 pl-3 text-left font-sans">
                <LeanText lean={r.lean} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {books.length > 0 && <span className="sr-only">Books: {books.join(", ")}</span>}
    </div>
  );
}

function modelPct(r: LineRow): string {
  const priced = r.books.find((b) => b.line === r.line && b.pricing)?.pricing;
  return priced ? pct(priced.p_model_over) : "—";
}

export function GameLinesPanel({ lines }: { lines: GameLines }) {
  if (lines.reason) {
    return (
      <p className="text-sm text-muted">
        {lines.reason === "not_connected"
          ? "Sportsbook lines aren't connected. Add the ODDS_API_KEY secret to turn them on (see docs/setup.md)."
          : "No lines for this game yet. Props usually post the day before; the credit budget fetches the soonest games first."}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      {lines.markets.map((m) => (
        <div key={m.market}>
          <h3 className="mb-1 text-xs font-semibold text-muted">{m.label}</h3>
          <MarketTable m={m} books={lines.books} />
        </div>
      ))}
      <p className="text-xs text-muted">
        {lines.books.join(" and ")} · fetched {lines.fetched_at ? localTime(lines.fetched_at) : "—"}. Best price at the
        common line is highlighted. No-vig = the books' implied probability with their margin removed (median across
        books). Model = RinkX's probability at the same line (pushes excluded). A lean is shown only when the edge is
        at least 3 points, the expected value at the offered price is positive and the data is good enough. Statistical
        estimates, not guarantees; prices change, so check the book before acting.
      </p>
    </div>
  );
}

/** Step chart of the over/yes implied probability per book over time. */
function Movement({ row }: { row: LineRow }) {
  const series = row.books
    .map((b, i) => ({
      book: b.book_name,
      color: BOOK_COLORS[i % BOOK_COLORS.length]!,
      pts: (b.movement ?? []).filter((m) => m.over !== null && m.status === "open"),
    }))
    .filter((s) => s.pts.length > 0);
  const all = series.flatMap((s) => s.pts);
  if (all.length < 2) return <p className="text-[11px] text-muted">No movement yet (one snapshot).</p>;
  const t = all.map((p) => Date.parse(p.at));
  const t0 = Math.min(...t);
  const t1 = Math.max(...t, t0 + 1);
  const ys = all.map((p) => implied(p.over!));
  const lo = Math.min(...ys) - 0.02;
  const hi = Math.max(...ys) + 0.02;
  const W = 280;
  const H = 64;
  const x = (at: string) => 8 + ((Date.parse(at) - t0) / (t1 - t0)) * (W - 60);
  const y = (p: number) => 6 + (1 - (implied(p) - lo) / (hi - lo)) * (H - 12);
  // End labels: keep at least 10px apart so close prices don't overprint.
  const labelY = new Map<string, number>();
  [...series]
    .map((s) => ({ book: s.book, y: y(s.pts[s.pts.length - 1]!.over!) + 3 }))
    .sort((a, b) => a.y - b.y)
    .forEach((l, i, arr) => {
      const prev = i > 0 ? labelY.get(arr[i - 1]!.book)! : -Infinity;
      labelY.set(l.book, Math.max(l.y, prev + 10));
    });
  return (
    <figure className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-16 w-full max-w-[320px]" role="img" aria-label="Line movement">
        <line x1={8} x2={W - 52} y1={H - 6} y2={H - 6} stroke="currentColor" strokeOpacity={0.15} />
        {series.map((s) => {
          let d = "";
          s.pts.forEach((p, i) => {
            const px = x(p.at);
            const py = y(p.over!);
            d += i === 0 ? `M${px},${py}` : `H${px}V${py}`;
          });
          const last = s.pts[s.pts.length - 1]!;
          d += `H${W - 52}`;
          return (
            <g key={s.book}>
              <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
              {s.pts.map((p) => (
                <circle key={p.at} cx={x(p.at)} cy={y(p.over!)} r={4} fill={s.color} stroke="#18202b" strokeWidth={2}>
                  <title>
                    {`${s.book} · ${localTime(p.at)} · ${p.line ?? ""} ${american(p.over)} (${pct(implied(p.over!))})`}
                  </title>
                </circle>
              ))}
              <text x={W - 48} y={labelY.get(s.book)} className="fill-muted text-[9px]">
                {american(last.over)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="flex flex-wrap gap-3 text-[11px] text-muted">
        {series.map((s) => (
          <span key={s.book} className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: s.color }} />
            {s.book}
          </span>
        ))}
        <span>implied probability of the over/yes price</span>
      </figcaption>
    </figure>
  );
}

export function PlayerLinesPanel({ markets }: { markets: LineMarket[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {markets.map((m) =>
        m.rows.map((r) => {
          const [a, b] = sides(m.kind);
          return (
            <article
              key={m.market}
              className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3"
              aria-label={`${m.label} line`}
            >
              <header className="flex items-baseline justify-between">
                <h3 className="text-sm font-semibold">{m.label}</h3>
                {r.line !== null && <span className="num text-sm">{r.line}</span>}
              </header>
              <ul className="num text-sm">
                {r.books.map((x) => (
                  <li key={x.book} className="flex justify-between">
                    <span className="font-sans">{x.book_name}</span>
                    <span>
                      {x.line !== r.line && <span className="text-[10px] text-muted">{x.line} </span>}
                      {a} {american(x.over)} · {b} {american(x.under)}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="num text-xs text-muted">
                No-vig {a.toLowerCase()}: {r.consensus ? pct(r.consensus.p_over) : "one-sided (margin can't be removed)"}
              </p>
              <ModelVsMarket row={r} a={a} b={b} />
              <Movement row={r} />
            </article>
          );
        }),
      )}
    </div>
  );
}

const PART_LABEL: Record<string, string> = {
  edge_strength: "Edge vs its uncertainty",
  role_certainty: "Role certainty",
  data_quality: "Data quality",
  market_agreement: "Market agreement",
  availability: "Availability",
};

export function ConfidenceBreakdown({ p }: { p: NonNullable<Pricing["confidence_parts"]> }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {Object.entries(p.parts).map(([k, v]) => (
        <li key={k}>
          <div className="flex items-center justify-between text-xs">
            <span>{PART_LABEL[k] ?? k}</span>
            <span className="num">
              {v}/{p.max[k]}
            </span>
          </div>
          <div className="mt-0.5 h-1 rounded bg-line" aria-hidden>
            <div className="h-1 rounded bg-accent" style={{ width: `${(v / (p.max[k] ?? 1)) * 100}%` }} />
          </div>
          {(p.notes[k] ?? []).map((n) => (
            <p key={n} className="text-[11px] text-muted">
              {n}
            </p>
          ))}
        </li>
      ))}
    </ul>
  );
}

/** Model vs market for the book with the lean (or the first priced book at the common line). */
function ModelVsMarket({ row, a, b }: { row: LineRow; a: string; b: string }) {
  const book =
    row.books.find((x) => x.book === row.lean?.book) ?? row.books.find((x) => x.line === row.line && x.pricing);
  const p = book?.pricing;
  if (!book || !p) return <p className="text-xs text-muted">Not priced: no current projection for this line.</p>;
  const market = p.p_novig_over ?? p.p_implied_over;
  return (
    <div className="flex flex-col gap-2 rounded border border-line p-2">
      <p className="num text-xs">
        <span className="text-muted">Model {a.toLowerCase()}</span> {pct(p.p_model_over)}{" "}
        <span className="text-muted">vs market</span> {market !== null ? pct(market) : "—"}
        {p.p_novig_over === null && <span className="text-muted"> (raw implied, margin included)</span>}
        <span className="text-muted"> at {book.book_name}</span>
      </p>
      <p className="num text-xs">
        <span className="text-muted">{a}:</span> edge {pts(p.edge_over)} pts, EV{" "}
        {p.ev_over !== null ? `${p.ev_over >= 0 ? "+" : "−"}${Math.abs(p.ev_over).toFixed(2)}u` : "—"}
        {p.edge_under !== null && (
          <>
            {" · "}
            <span className="text-muted">{b}:</span> edge {pts(p.edge_under)} pts, EV{" "}
            {p.ev_under !== null ? `${p.ev_under >= 0 ? "+" : "−"}${Math.abs(p.ev_under).toFixed(2)}u` : "—"}
          </>
        )}
      </p>
      <p className="text-sm">
        {p.side === "none" ? (
          <span className="text-muted">No lean at this price.</span>
        ) : (
          <>
            Lean <span className="font-semibold">{SIDE_LABEL[p.side]}</span> · confidence{" "}
            <span className="num font-semibold">{p.confidence}</span>/100
          </>
        )}
      </p>
      {p.confidence_parts && (
        <details className="text-xs">
          <summary className="cursor-pointer text-accent">Confidence breakdown ({p.confidence}/100)</summary>
          <div className="mt-2">
            <ConfidenceBreakdown p={p.confidence_parts} />
          </div>
        </details>
      )}
      {p.calculation && (
        <details className="text-xs">
          <summary className="cursor-pointer text-accent">Show the calculation</summary>
          <ol className="num mt-2 list-decimal pl-5 text-[11px] text-muted">
            {p.calculation.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
