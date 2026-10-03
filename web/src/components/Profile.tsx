import { useState } from "react";
import type { GoalieImpact, PropRow, ShotMap, SkaterProfile } from "../lib/data/types";
import { mmss } from "../lib/format";
import { hitRate } from "../lib/propFilters";
import { betText } from "../routes/BestProps";
import { odds, pts } from "./Lines";
import { GoalieImpactLine, pct } from "./Projections";
import { Panel } from "./ui";

const n2 = (x: number | null | undefined, d = 2) => (x == null ? "—" : x.toFixed(d));

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className="num">{value}</dd>
    </div>
  );
}

export function PropProfile({ p, props }: { p: SkaterProfile; props: PropRow[] }) {
  const s = p.shooting;
  const u = p.usage;
  const m = p.matchup;
  return (
    <Panel title="Prop profile">
      <div className="flex flex-col gap-4" aria-label="Prop profile">
        <section aria-label="Shooting">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Shooting</h3>
          {s.games === 0 ? (
            <p className="text-sm text-muted">INSUFFICIENT DATA: no games this season.</p>
          ) : (
            <dl className="grid grid-cols-3 gap-2 text-sm sm:grid-cols-5">
              <Stat label="SOG / game" value={n2(s.sog_per_game)} />
              <Stat label="SOG / 60" value={n2(s.sog_per_60)} />
              <Stat label="Shot attempts / game" value={n2(s.attempts_per_game)} />
              <Stat label="Share of team SOG" value={s.shot_share == null ? "—" : pct(s.shot_share)} />
              <Stat label="Last 5 / last 10" value={`${n2(s.l5_avg, 1)} / ${n2(s.l10_avg, 1)}`} />
              <Stat label="Home / away" value={`${n2(s.home_avg, 1)} / ${n2(s.away_avg, 1)}`} />
              <Stat label="Recent SOG" value={s.recent_sog.join(" ") || "—"} />
            </dl>
          )}
        </section>
        <section aria-label="Usage">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Usage</h3>
          <dl className="grid grid-cols-3 gap-2 text-sm">
            <Stat label="TOI (season)" value={mmss(u.toi_avg_s)} />
            <Stat label="TOI (last 5)" value={mmss(u.toi_l5_s)} />
            <Stat label="PP TOI" value={mmss(u.pp_toi_avg_s)} />
          </dl>
          {u.recent.length ? (
            <p className="num mt-1 text-xs">
              Recent deployment (from shift charts):{" "}
              {u.recent.map((d) => `${d.date.slice(5)} ${d.line ?? "—"}${d.pp ? `/${d.pp}` : ""}`).join(" · ")}
            </p>
          ) : (
            <p className="mt-1 text-xs text-muted">Line and PP unit: DATA UNAVAILABLE (no shift charts loaded yet).</p>
          )}
        </section>
        <section aria-label="Matchup">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Matchup</h3>
          {m ? (
            <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <Stat label="Next" value={`${m.home ? "vs" : "@"} ${m.opponent} · ${m.date}`} />
              <Stat label="Opponent SOG allowed" value={`${n2(m.opp_sog_allowed, 1)} (lg ${n2(m.league_sog, 1)})`} />
              <Stat
                label={`To ${m.position_group} (last 20)`}
                value={m.opp_vs_position == null ? "—" : `${m.opp_vs_position >= 1 ? "+" : "−"}${Math.abs((m.opp_vs_position - 1) * 100).toFixed(0)}% vs league`}
              />
              <Stat label="Home / away" value={m.home ? "Home" : "Away"} />
            </dl>
          ) : (
            <p className="text-sm text-muted">No upcoming game scheduled.</p>
          )}
        </section>
        <section aria-label="Props">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Props</h3>
          {props.length === 0 ? (
            <p className="text-sm text-muted">DATA UNAVAILABLE: no lines priced for his next game.</p>
          ) : (
            <table className="num w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="text-left font-normal">Line</th>
                  <th className="text-right font-normal">Price</th>
                  <th className="text-right font-normal">Proj</th>
                  <th className="text-right font-normal">Model</th>
                  <th className="text-right font-normal">Edge</th>
                  <th className="text-right font-normal">L10</th>
                </tr>
              </thead>
              <tbody>
                {props.map((r) => {
                  const h = hitRate(r);
                  return (
                    <tr key={r.prediction_id}>
                      <td>
                        {betText(r)} <span className="text-muted">{r.book_name}</span>
                      </td>
                      <td className="text-right">{odds(r.price)}</td>
                      <td className="text-right">{n2(r.projection)}</td>
                      <td className="text-right">{pct(r.p_model)}</td>
                      <td className="text-right">{pts(r.edge)}</td>
                      <td className="text-right">{h === null ? "—" : `${Math.round(h * 100)}%`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
        <ShotMapView m={p.shot_map} />
      </div>
    </Panel>
  );
}

export function GoalieProfile({ i }: { i: GoalieImpact | null }) {
  return (
    <Panel title="Goalie impact">
      <GoalieImpactLine i={i} />
    </Panel>
  );
}

type Window = "l5" | "l10" | "season" | "opp";
const COLORS: Record<string, string> = { goal: "#f2c94c", shot: "#4fa3ff", miss: "#8a96a8", block: "#8a96a8" };

/** Half rink (offensive zone toward +x): x 25..100, y -42.5..42.5 ft. */
export function ShotMapView({ m }: { m: ShotMap }) {
  const [win, setWin] = useState<Window>("season");
  const [opp, setOpp] = useState("");
  const [density, setDensity] = useState(false);
  const opps = Array.from(new Set(m.events.map((e) => e.opp))).sort();
  const ev = m.events.filter((e) =>
    win === "l5" ? e.g < 5 : win === "l10" ? e.g < 10 : win === "opp" ? e.opp === (opp || opps[0]) : true,
  );
  if (m.with_coordinates === 0)
    return (
      <section aria-label="Shot map">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Shot map</h3>
        <p className="text-sm text-muted">DATA UNAVAILABLE: no shot locations in the play-by-play loaded for him.</p>
      </section>
    );
  const X0 = 25;
  const W = 300;
  const H = 340;
  const sx = (x: number) => ((x - X0) / 75) * W;
  const sy = (y: number) => ((42.5 - y) / 85) * H;
  const cells = new Map<string, number>();
  if (density)
    for (const e of ev) {
      const k = `${Math.floor((e.x - X0) / 5)}|${Math.floor((e.y + 42.5) / 5)}`;
      cells.set(k, (cells.get(k) ?? 0) + 1);
    }
  const maxCell = Math.max(1, ...cells.values());
  const counts = { goal: 0, shot: 0, attempt: 0, hd: 0 };
  for (const e of ev) {
    if (e.t === "goal") counts.goal++;
    if (e.t === "shot" || e.t === "goal") counts.shot++;
    counts.attempt++;
    if (e.hd) counts.hd++;
  }
  return (
    <section aria-label="Shot map">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Shot map</h3>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs" role="group" aria-label="Shot map filters">
        {(["l5", "l10", "season", "opp"] as Window[]).map((w) => (
          <button key={w} type="button" aria-pressed={win === w} onClick={() => setWin(w)}
            className={`min-h-9 rounded-full border px-3 ${win === w ? "border-accent bg-accent/15" : "border-line text-muted"}`}>
            {{ l5: "Last 5", l10: "Last 10", season: "Season", opp: "vs opponent" }[w]}
          </button>
        ))}
        {win === "opp" && (
          <select value={opp || opps[0]} onChange={(e) => setOpp(e.target.value)} className="min-h-9 rounded border border-line bg-panel px-2">
            {opps.map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
        )}
        <label className="flex min-h-9 items-center gap-1">
          <input type="checkbox" checked={density} onChange={(e) => setDensity(e.target.checked)} /> Density
        </label>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[360px] rounded border border-line bg-panel-2" role="img"
        aria-label={`Shot map: ${counts.attempt} attempts, ${counts.shot} on goal, ${counts.goal} goals`}>
        <line x1={sx(89)} x2={sx(89)} y1={sy(42.5)} y2={sy(-42.5)} stroke="#c0392b" strokeOpacity={0.6} />
        <line x1={sx(25)} x2={sx(25)} y1={0} y2={H} stroke="#2e6bd6" strokeOpacity={0.6} strokeWidth={3} />
        <rect x={sx(89)} y={sy(3)} width={sx(92) - sx(89)} height={sy(-3) - sy(3)} fill="none" stroke="currentColor" strokeOpacity={0.5} />
        <circle cx={sx(69)} cy={sy(22)} r={(15 / 75) * W} fill="none" stroke="#c0392b" strokeOpacity={0.3} />
        <circle cx={sx(69)} cy={sy(-22)} r={(15 / 75) * W} fill="none" stroke="#c0392b" strokeOpacity={0.3} />
        <path d={`M${sx(89)},${sy(22)} L${sx(64)},${sy(22)} L${sx(64)},${sy(-22)} L${sx(89)},${sy(-22)}`} fill="#f2c94c" fillOpacity={0.06} stroke="#f2c94c" strokeOpacity={0.3} strokeDasharray="3 3" />
        {density
          ? [...cells].map(([k, c]) => {
              const [i, j] = k.split("|").map(Number) as [number, number];
              return <rect key={k} x={sx(X0 + i * 5)} y={sy(-42.5 + (j + 1) * 5)} width={(5 / 75) * W} height={(5 / 85) * H}
                fill="#4fa3ff" fillOpacity={0.15 + 0.75 * (c / maxCell)} />;
            })
          : ev.map((e, i) => (
              <circle key={i} cx={sx(Math.max(X0, e.x))} cy={sy(e.y)} r={e.t === "goal" ? 5 : 3.5}
                fill={e.t === "goal" || e.t === "shot" ? COLORS[e.t] : "none"} stroke={COLORS[e.t]} strokeWidth={e.hd ? 2 : 1}>
                <title>{`${e.t}${e.hd ? " (high danger)" : ""} vs ${e.opp}`}</title>
              </circle>
            ))}
      </svg>
      <p className="num mt-1 text-xs">
        {counts.attempt} attempts · {counts.shot} on goal · {counts.goal} goals · {counts.hd} high danger
      </p>
      <p className="text-[11px] text-muted">
        Filled: on goal (gold = goal); hollow: missed or blocked; thick ring: high danger. {m.definition} Dashed box: the
        slot. {m.without_coordinates > 0 && `${m.without_coordinates} attempts had no location and aren't drawn.`}
      </p>
    </section>
  );
}
