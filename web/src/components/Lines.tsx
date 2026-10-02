import type { BookLine, GameLines, LineMarket, LineRow } from "../lib/data/types";
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
            </tr>
          ))}
        </tbody>
      </table>
      {books.length > 0 && <span className="sr-only">Books: {books.join(", ")}</span>}
    </div>
  );
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
        books). Prices change; check the book before acting.
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
              <Movement row={r} />
            </article>
          );
        }),
      )}
    </div>
  );
}
