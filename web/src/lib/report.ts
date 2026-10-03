// Daily NHL report: every line is built from a published number (props, line movement, news,
// starting goalies). Nothing is inferred beyond those numbers; an empty section says so.
import { cents } from "../components/Lines";
import type { GameSummary, MovementRow, NewsItem, PropRow } from "./data/types";
import { bestPerProp } from "../components/PropGroups";

export interface ReportSection {
  title: string;
  lines: string[];
  empty: string;
}

const am = (p: number | null) => (p === null ? "—" : p > 0 ? `+${p}` : `−${Math.abs(p)}`);
const pc = (p: number) => `${(p * 100).toFixed(1)}%`;
const side = (r: PropRow) => {
  const s = r.lean ?? r.side_scored;
  if (r.market === "game_moneyline") return `${s === "home" ? r.game.home : r.game.away} to win`;
  const word = s === "over" ? "Over" : s === "under" ? "Under" : s === "yes" ? "Yes" : s === "no" ? "No" : s;
  return `${word}${r.line !== null ? ` ${r.line}` : ""} ${r.market_label}`;
};

export function propLine(r: PropRow): string {
  const who = r.subject.type === "player" ? `${r.subject.name} (${r.subject.team ?? ""}) ` : `${r.game.away} @ ${r.game.home}: `;
  const bits = [`${who}${side(r)} ${am(r.price)} at ${r.book_name}`, `model ${pc(r.p_model)}`];
  if (r.p_market !== null) bits.push(`market ${pc(r.p_market)}`);
  if (r.edge !== null) bits.push(`edge ${r.edge >= 0 ? "+" : ""}${(r.edge * 100).toFixed(1)} pts`);
  if (r.scores?.intelligence != null) bits.push(`Prop Intelligence ${r.scores.intelligence}/100`);
  return bits.join(" · ");
}

const MARKETS = {
  sog: ["skater_shots_on_goal"],
  goal: ["skater_goals", "skater_anytime_goal", "skater_first_goal"],
  point: ["skater_points"],
  assist: ["skater_assists"],
};

export function buildReport(input: {
  props: PropRow[];
  moves: MovementRow[];
  news: NewsItem[];
  games: GameSummary[];
  stale: boolean;
}): ReportSection[] {
  const leans = bestPerProp(input.props.filter((r) => r.lean));
  const byScore = [...leans].sort((a, b) => (b.scores?.intelligence ?? -1) - (a.scores?.intelligence ?? -1));
  const top = (markets: string[], n = 3) => byScore.filter((r) => markets.includes(r.market)).slice(0, n).map(propLine);
  const out: ReportSection[] = [
    { title: "Best props today", lines: byScore.slice(0, 5).map(propLine), empty: "No prop clears the bar today (edge ≥ 3 pts with positive EV)." },
    { title: "Best SOG props", lines: top(MARKETS.sog), empty: "No shots-on-goal lean today." },
    { title: "Best goal props", lines: top(MARKETS.goal), empty: "No goal lean today." },
    { title: "Best point props", lines: top(MARKETS.point), empty: "No points lean today." },
    { title: "Best assist props", lines: top(MARKETS.assist), empty: "No assists lean today." },
  ];
  const moves = [...input.moves]
    .filter((m) => m.first.over !== null && m.now.over !== null && m.change_pts !== null)
    .sort((a, b) => Math.abs(b.change_pts!) - Math.abs(a.change_pts!))
    .slice(0, 5)
    .map((m) => {
      const c = cents(m.first.over!, m.now.over!);
      return `${m.subject} ${m.line ?? ""} ${m.market_label} at ${m.book_name}: ${am(m.first.over)} → ${am(m.now.over)} (${Math.abs(c)} cents; over implied ${m.change_pts! >= 0 ? "+" : ""}${m.change_pts!.toFixed(1)} pts)`;
    });
  out.push({ title: "Biggest market moves", lines: moves, empty: "No line has moved since it opened." });
  const lineup = input.news
    .filter((n) => ["injury", "lineup", "scratch", "goalie", "suspension", "rest"].includes(n.category))
    .slice(0, 8)
    .map((n) => `${n.headline} (${n.reliability.replace("_", " ")}, ${n.url})`);
  for (const g of input.games)
    for (const s of [g.away, g.home])
      if (s.goalie?.status === "confirmed") lineup.push(`${s.team.abbrev}: ${s.goalie.name} confirmed to start${s.goalie.source ? ` (${s.goalie.source})` : ""}`);
  out.push({ title: "Lineup news", lines: lineup, empty: "No lineup news or confirmed goalies entered yet." });
  const value = [...leans]
    .filter((r) => r.ev !== null)
    .sort((a, b) => b.ev! - a.ev!)
    .slice(0, 5)
    .map((r) => `${propLine(r)} · EV ${r.ev! >= 0 ? "+" : ""}${r.ev!.toFixed(2)} per unit`);
  out.push({ title: "Value opportunities", lines: value, empty: "No positive-EV lean today." });
  const flags: string[] = [];
  if (input.stale) flags.push("The published data is older than it should be: treat prices and projections as possibly out of date.");
  for (const r of leans) {
    const why: string[] = [];
    if ((r.edge ?? 0) >= 0.15) why.push(`edge of ${(r.edge! * 100).toFixed(1)} pts is unusually large (often a data or line issue)`);
    if ((r.confidence ?? 100) < 50) why.push(`confidence only ${r.confidence}/100`);
    if (r.missing_inputs.includes("goalie_unconfirmed")) why.push("opposing goalie not confirmed");
    if (r.missing_inputs.includes("injury_day_to_day")) why.push("listed day-to-day");
    if (why.length) flags.push(`${propLine(r)}: ${why.join("; ")}`);
  }
  out.push({ title: "Red flags", lines: flags.slice(0, 8), empty: "No red flags on today's leans." });
  return out;
}

export function reportText(date: string, sections: ReportSection[]): string {
  return [
    `RinkX daily NHL report · ${date}`,
    "Statistical estimates from published data; props like these lose often.",
    "",
    ...sections.flatMap((s) => [s.title.toUpperCase(), ...(s.lines.length ? s.lines.map((l) => `• ${l}`) : [`• ${s.empty}`]), ""]),
  ].join("\n");
}
