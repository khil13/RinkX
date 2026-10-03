import { Link } from "react-router";
import type { InjuryEntry } from "../lib/data/types";
import { localTime, longDate } from "../lib/format";

export const INJURY_LABEL: Record<InjuryEntry["status"], string> = {
  out: "OUT",
  injured_reserve: "IR",
  long_term_ir: "LTIR",
  day_to_day: "DAY-TO-DAY",
  questionable: "QUESTIONABLE",
  suspended: "SUSPENDED",
  unknown: "LISTED",
};

export function InjuryChip({ e }: { e: InjuryEntry }) {
  const soft = e.status === "day_to_day" || e.status === "questionable";
  return (
    <span
      className={`num rounded border px-1.5 py-0.5 text-[10px] font-semibold ${soft ? "border-warn/40 text-warn" : "border-bad/40 text-bad"}`}
    >
      {INJURY_LABEL[e.status]}
    </span>
  );
}

export function InjuryLine({ e, link = true }: { e: InjuryEntry; link?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {link ? (
          <Link to={`/players/${e.player.id}`} className="hover:text-accent">
            {e.player.name}
          </Link>
        ) : null}
        <span className="num text-xs text-muted">{e.player.position}</span>
        <InjuryChip e={e} />
        {e.expected_return && (
          <span className="num text-xs text-muted">expected back {longDate(e.expected_return)}</span>
        )}
      </div>
      {e.description && <p className="text-xs text-muted">{e.description}</p>}
      <p className="text-[11px] text-muted">
        {e.source_name}, {localTime(e.reported_at)} ·{" "}
        <a href={e.source} target="_blank" rel="noreferrer" className="underline">
          source
        </a>
      </p>
    </div>
  );
}
