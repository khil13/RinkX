import { teamColors } from "../lib/teamColors";

/** Team abbreviation on its own colours (the text colour is picked for contrast). */
export function TeamChip({ abbrev, className = "" }: { abbrev: string | null | undefined; className?: string }) {
  if (!abbrev) return null;
  const c = teamColors(abbrev);
  return (
    <span
      className={`num inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-bold tracking-wide ${className}`}
      style={{ background: c.primary, color: c.text, boxShadow: `inset 0 -2px 0 ${c.secondary}` }}
      data-team={abbrev}
    >
      {abbrev}
    </span>
  );
}

/** A left-edge stripe in one team's colours, or split between two teams (game totals). Put it
 * inside a `relative overflow-hidden` card. */
export function TeamStripe({ teams }: { teams: (string | null | undefined)[] }) {
  const cs = teams.filter(Boolean).map((t) => teamColors(t));
  if (cs.length === 0) return null;
  const [a, b] = cs;
  const background =
    cs.length === 1 || !b
      ? `linear-gradient(to bottom, ${a!.primary} 0 72%, ${a!.secondary} 72% 100%)`
      : `linear-gradient(to bottom, ${a!.primary} 0 50%, ${b.primary} 50% 100%)`;
  return <span aria-hidden className="absolute inset-y-0 left-0 w-1.5" style={{ background }} />;
}
