import { useMemo, useState } from "react";
import { Link } from "react-router";
import { odds, pts, SIDE_LABEL } from "../components/Lines";
import { pct } from "../components/Projections";
import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted, useEncryptedMany, useManifest } from "../lib/data/fetch";
import { build, type Built, corrLabel, RISK, type Risk } from "../lib/autoParlay";
import type { BestProps } from "../lib/data/types";
import { betText } from "./BestProps";
import { type Correlations, evaluate, type GroupUsed, type Leg, MAX_LEGS, type PairSource, toAmerican } from "../lib/parlay";
import type { SimInputs } from "../lib/sim";
import { parlayStore, useParlayLegs } from "../lib/parlayStore";
import { clock, longDate } from "../lib/format";

const SOURCE_LABEL: Record<PairSource, string> = {
  estimate: "estimated",
  different_games: "different games: independent",
  not_estimated: "no estimate: treated as independent",
  too_little_data: "too little data: treated as independent",
};

const RELATION_LABEL: Record<string, string> = {
  same_player: "same player",
  same_team: "teammates",
  opponent: "opponents",
  opp_goalie: "skater vs opposing goalie",
};

function legText(l: Leg) {
  if (l.market === "game_moneyline") return `${l.side === "home" ? l.home : l.away} to win`;
  return `${SIDE_LABEL[l.side] ?? l.side}${l.line !== null ? ` ${l.line}` : ""} ${l.market_label}`;
}

const signedPct = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%`;

const N_TEXT = "20,000";
const WHY_NOT: Record<NonNullable<GroupUsed["why_not_sim"]>, string> = {
  no_sim: "no simulation published for this game",
  unsupported_leg: "a leg the simulation can't settle (hits, blocks, power-play stats, a backup goalie)",
  too_rare: "the legs win together too rarely to simulate reliably",
};

function GroupLine({ g, legs }: { g: GroupUsed; legs: Leg[] }) {
  const first = legs[g.legs[0]!]!;
  const which = g.legs.map((i) => i + 1).join(", ");
  return (
    <li className="flex flex-wrap justify-between gap-2">
      <span>
        <span className="num text-muted">Legs {which}</span> · {first.away} @ {first.home}
      </span>
      <span className="num text-xs text-muted">
        {g.method === "simulation" ? (
          <>
            simulated: together {((g.together! / g.n!) * 100).toFixed(1)}% of {g.n!.toLocaleString()} games, ×
            {g.lift!.toFixed(2)} vs independent · Correlation: {corrLabel(g.lift! - 1)}
          </>
        ) : (
          <>correlations ({WHY_NOT[g.why_not_sim ?? "no_sim"]})</>
        )}
      </span>
    </li>
  );
}

function AutoBuilder() {
  const props = useEncrypted<BestProps>("props/best.json");
  const corrRes = useEncrypted<Correlations>("correlations.json");
  const manifest = useManifest().data;
  const [legsN, setLegsN] = useState(3);
  const [risk, setRisk] = useState<Risk>("balanced");
  const [market, setMarket] = useState("");
  const [built, setBuilt] = useState<Built | null>(null);
  if (props.state !== "ready") return null;
  const rows = props.value.data.rows;
  const corr = corrRes.state === "ready" ? corrRes.value.data : null;
  const markets = Array.from(new Set(rows.map((r) => `${r.market}|${r.market_label}`)));
  const date = manifest?.slate_date && rows.some((r) => r.game.date === manifest.slate_date) ? manifest.slate_date : null;
  const run = (n: number, rk: Risk, mk: string) =>
    setBuilt(build(rows, corr, { legs: n, risk: rk, markets: mk ? [mk] : null, date }));
  const sel = "min-h-9 rounded border border-line bg-panel px-2 text-sm text-text";
  return (
    <Panel title="Build from the slate">
      <div className="flex flex-wrap items-end gap-2" role="group" aria-label="Parlay builder options">
        <label className="flex flex-col text-[11px] text-muted">
          Market
          <select className={sel} value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="">All markets</option>
            {markets.map((m) => {
              const [code, label] = m.split("|");
              return (
                <option key={code} value={code}>
                  {label}
                </option>
              );
            })}
          </select>
        </label>
        <label className="flex flex-col text-[11px] text-muted">
          Legs
          <select className={sel} value={legsN} onChange={(e) => setLegsN(Number(e.target.value))}>
            {[2, 3, 4, 5, 6, 7, 8].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-[11px] text-muted">
          Risk
          <select className={sel} value={risk} onChange={(e) => setRisk(e.target.value as Risk)}>
            {(Object.keys(RISK) as Risk[]).map((k) => (
              <option key={k} value={k}>
                {RISK[k].label}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="min-h-9 rounded-md border border-accent/60 px-3 text-sm" onClick={() => run(legsN, risk, market)}>
          Build
        </button>
        <button type="button" className="min-h-9 rounded-md border border-accent bg-accent/15 px-3 text-sm font-semibold" onClick={() => run(8, "balanced", "")}>
          🔥 BUILD MY 8-LEG
        </button>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        {RISK[risk].label}: legs with model probability ≥ {Math.round(RISK[risk].minProb * 100)}%, edge ≥{" "}
        {Math.round(RISK[risk].minEdge * 100)} pts, Prop Intelligence ≥ {RISK[risk].minScore}, confidence ≥{" "}
        {RISK[risk].minConfidence}, at most {RISK[risk].maxPerGame} per game, no price that moved sharply against
        the leg; each step adds the leg that most improves {RISK[risk].objective}, counting how legs move together.
        The 8-leg build uses Balanced across every market{date ? ` on ${longDate(date)}` : ""}.
      </p>
      {built && (
        <div className="mt-3 flex flex-col gap-2" aria-label="Built parlay">
          {built.legs.length === 0 ? (
            <Notice>
              INSUFFICIENT DATA: no prop meets the {RISK[risk].label.toLowerCase()} rules ({built.eligible} eligible of{" "}
              {built.considered} priced lines). Nothing is forced in.
            </Notice>
          ) : (
            <>
              {built.short && (
                <p className="text-xs text-warn">
                  Only {built.legs.length} legs meet the rules; nothing weaker was added to reach the number asked for.
                </p>
              )}
              <table className="num w-full text-xs">
                <thead className="text-muted">
                  <tr>
                    <th className="text-left font-normal">Leg</th>
                    <th className="text-right font-normal">Odds</th>
                    <th className="text-right font-normal">Model</th>
                    <th className="text-right font-normal">Market</th>
                    <th className="text-right font-normal">Edge</th>
                    <th className="text-right font-normal">PI</th>
                  </tr>
                </thead>
                <tbody>
                  {built.legs.map(({ row: r }) => (
                    <tr key={r.prediction_id}>
                      <td className="py-0.5">
                        <span className="font-semibold">{r.subject.type === "player" ? r.subject.name : `${r.game.away} @ ${r.game.home}`}</span>{" "}
                        {betText(r)} <span className="text-muted">· {r.book_name}</span>
                      </td>
                      <td className="text-right">{odds(r.price)}</td>
                      <td className="text-right">{pct(r.p_model)}</td>
                      <td className="text-right">{r.p_market !== null ? pct(r.p_market) : "—"}</td>
                      <td className="text-right">{pts(r.edge)}</td>
                      <td className="text-right">{r.scores?.intelligence ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {built.result && (
                <p className="num text-sm">
                  Estimated combined probability <span className="font-semibold">{pct(built.result.p_adjusted)}</span> (if
                  independent {pct(built.result.p_independent)}) · prices multiplied{" "}
                  {odds(toAmerican(built.result.offered_decimal))} · EV {built.result.ev >= 0 ? "+" : "−"}
                  {Math.abs(built.result.ev * 100).toFixed(1)}%
                </p>
              )}
              <button
                type="button"
                className="min-h-9 self-start rounded-md border border-line px-3 text-sm hover:border-accent/50"
                onClick={() => {
                  parlayStore.clear();
                  built.legs.forEach((x) => parlayStore.add(x.leg));
                }}
              >
                Use these legs
              </button>
              <p className="text-[11px] text-muted">
                The estimate here uses correlations; once the legs are added below, legs in the same game are
                re-combined with the same-game simulation. Long parlays lose most of the time even when every leg is
                priced well.
              </p>
            </>
          )}
        </div>
      )}
    </Panel>
  );
}

export function Parlay() {
  const legs = useParlayLegs();
  const res = useEncrypted<Correlations>("correlations.json");
  const games = [...new Set(legs.map((l) => l.game_id))];
  const simRes = useEncryptedMany<SimInputs>(games.map((g) => `sim/${g}.json`));
  const simsLoading = Object.values(simRes).some((x) => x.state === "loading");
  const corr = res.state === "ready" ? res.value.data : null;
  const sims = useMemo(() => {
    const out: Record<number, SimInputs | null> = {};
    for (const g of games) {
      const x = simRes[`sim/${g}.json`];
      out[g] = x?.state === "ready" ? x.value.data : null;
    }
    return out;
  }, [JSON.stringify(games), simsLoading]);
  const r = useMemo(
    () => (legs.length > 0 && !simsLoading ? evaluate(legs.slice(0, MAX_LEGS), corr, sims) : null),
    [legs, corr, sims, simsLoading],
  );
  if (res.state === "loading" || simsLoading) return <Spinner label="Loading correlations…" />;
  const started = legs.filter((l) => Date.parse(l.start_time_utc) <= Date.now());
  const leanCount = r?.pairs.filter((p) => p.source !== "estimate" && p.source !== "different_games").length ?? 0;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">Parlay builder</h1>
        <p className="text-sm text-muted">
          Combines RinkX's probabilities for each leg, adjusted for how such props have moved together in past games.
          Add legs from a prop on Props or the Card of the Day. Legs stay on this device.
        </p>
      </header>

      <Notice tone="warn">
        Parlays multiply the bookmaker's margin and the variance: even when every leg is priced fairly, most parlays
        lose, and long losing runs are normal. Treat the numbers below as estimates with real uncertainty.
      </Notice>

      <AutoBuilder />

      {legs.length === 0 ? (
        <Notice>
          No legs yet. Open a prop on{" "}
          <Link to="/props" className="text-accent underline">
            Props
          </Link>{" "}
          or{" "}
          <Link to="/props/best" className="text-accent underline">
            Card of the Day
          </Link>{" "}
          and tap “Add to parlay”.
        </Notice>
      ) : (
        <>
          <Panel
            title={`Legs (${legs.length})`}
            right={
              <button type="button" onClick={() => parlayStore.clear()} className="min-h-9 text-xs text-muted underline">
                Clear all
              </button>
            }
          >
            <ol className="flex flex-col divide-y divide-line" aria-label="Parlay legs">
              {legs.map((l, i) => (
                <li key={l.key} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                  <span>
                    <span className="num mr-2 text-xs text-muted">{i + 1}</span>
                    <span className="font-semibold">{l.subject}</span> {legText(l)}{" "}
                    <span className="num font-semibold">{odds(l.price)}</span>
                    <span className="block text-xs text-muted">
                      {l.away} @ {l.home} · {longDate(l.start_time_utc.slice(0, 10))} {clock(l.start_time_utc)} ·{" "}
                      {l.book_name} · model {pct(l.p_model)}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => parlayStore.remove(l.key)}
                    className="min-h-9 rounded px-2 text-xs text-muted hover:text-text"
                    aria-label={`Remove ${l.subject} ${legText(l)}`}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ol>
            {legs.length > MAX_LEGS && (
              <p className="mt-2 text-xs text-warn">Only the first {MAX_LEGS} legs are combined.</p>
            )}
            {started.length > 0 && (
              <p className="mt-2 text-xs text-warn">
                {started.length} leg{started.length > 1 ? "s are" : " is"} for a game that has already started: the
                price shown is no longer available.
              </p>
            )}
          </Panel>

          {r && (
            <Panel title="Combined">
              <dl className="num grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4" aria-label="Parlay result">
                <div>
                  <dt className="text-xs text-muted">If independent</dt>
                  <dd>{pct(r.p_independent)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Adjusted for correlation</dt>
                  <dd className="font-semibold">{pct(r.p_adjusted)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Fair odds</dt>
                  <dd>{r.fair_decimal ? odds(toAmerican(r.fair_decimal)) : "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Legs' prices multiplied</dt>
                  <dd>
                    {odds(toAmerican(r.offered_decimal))}{" "}
                    <span className="text-xs text-muted">(EV {signedPct(r.ev)})</span>
                  </dd>
                </div>
              </dl>
              <p className="mt-3 text-xs text-muted">
                Books price same-game parlays their own way, usually with a bigger margin than the legs' prices
                multiplied. Compare the fair odds with the parlay price your book actually offers.
              </p>
              {r.shrink < 1 && (
                <p className="mt-1 text-xs text-warn">
                  These correlations didn't fit together exactly, so they were scaled toward independence by{" "}
                  {((1 - r.shrink) * 100).toFixed(0)}%.
                </p>
              )}
            </Panel>
          )}

          {r && r.groups.some((g) => g.method !== "single") && (
            <Panel title="Same-game legs">
              <ul className="flex flex-col gap-1.5 text-sm" aria-label="Same-game groups">
                {r.groups
                  .filter((g) => g.method !== "single")
                  .map((g) => (
                    <GroupLine key={g.game_id} g={g} legs={legs} />
                  ))}
              </ul>
              <p className="mt-2 text-[11px] text-muted">
                The simulation plays the game out {N_TEXT} times from the same projections: goals, who scored and
                assisted (linemates more often), shots and saves. It is used only for how the legs move together; each
                leg keeps its own model probability. Without a simulation for every leg, the correlations below are
                used.
              </p>
            </Panel>
          )}

          {r && r.pairs.length > 0 && (
            <Panel title="How the legs relate">
              <ul className="flex flex-col gap-1 text-sm" aria-label="Leg pairs">
                {r.pairs.map((p) => (
                  <li key={`${p.i}-${p.j}`} className="flex flex-wrap justify-between gap-2">
                    <span>
                      <span className="num text-muted">
                        {p.i + 1} & {p.j + 1}
                      </span>{" "}
                      {p.relation ? RELATION_LABEL[p.relation] : ""}
                    </span>
                    <span className="num text-xs text-muted">
                      {p.source === "estimate" ? (
                        <>
                          <span className="font-semibold text-text">Correlation: {corrLabel(p.rho, p.ci)}</span> · ρ {p.rho >= 0 ? "+" : "−"}
                          {Math.abs(p.rho).toFixed(2)} (95% {p.ci?.[0].toFixed(2)} to {p.ci?.[1].toFixed(2)}, n=
                          {p.n?.toLocaleString()})
                        </>
                      ) : (
                        SOURCE_LABEL[p.source] + (p.n ? ` (n=${p.n})` : "")
                      )}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] text-muted">
                ρ is how the two legs' results have moved together after taking out each player's usual level
                (positive: they tend to win together). Estimated from{" "}
                {corr?.window ? `${longDate(corr.window.from)} – ${longDate(corr.window.to)}` : "past games"}. Pairs
                with fewer than {corr?.min_n ?? 300} games, game-level legs, and a goalie with his own skaters are
                treated as independent{leanCount > 0 ? ", as marked" : ""}.
              </p>
              {!corr && (
                <p className="mt-1 text-xs text-warn">Correlations aren't published yet, so every leg is treated as independent.</p>
              )}
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
