import { useMemo } from "react";
import { Link } from "react-router";
import { odds, SIDE_LABEL } from "../components/Lines";
import { pct } from "../components/Projections";
import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted, useEncryptedMany } from "../lib/data/fetch";
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
            {g.lift!.toFixed(2)} vs independent
          </>
        ) : (
          <>correlations ({WHY_NOT[g.why_not_sim ?? "no_sim"]})</>
        )}
      </span>
    </li>
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
                          ρ {p.rho >= 0 ? "+" : "−"}
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
