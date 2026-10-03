import { useState } from "react";
import type { PropRow, ScorePart } from "../lib/data/types";

/** "🔥 94/100" at 80 and above; the plain number below that; nothing without enough data. */
export function ScoreBadge({ score, label }: { score: number | null | undefined; label: string }) {
  if (score == null) return null;
  return (
    <span
      className={`num inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
        score >= 80 ? "border-over/50 text-over" : score >= 60 ? "border-accent/40 text-text" : "border-line text-muted"
      }`}
      aria-label={`${label} ${score} of 100`}
      title={label}
    >
      {score >= 80 && <span aria-hidden>🔥</span>}
      {score}/100
    </span>
  );
}

function Parts({ parts, label }: { parts: ScorePart[]; label: string }) {
  const sorted = [...parts].sort((a, b) => (b.points ?? -1) - (a.points ?? -1));
  return (
    <table className="num w-full text-[11px]" aria-label={label}>
      <thead className="text-muted">
        <tr>
          <th className="text-left font-normal">Part</th>
          <th className="text-right font-normal">Weight</th>
          <th className="text-right font-normal">Score</th>
          <th className="text-right font-normal">Points</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => (
          <tr key={p.part} className={p.score === null ? "text-muted" : ""}>
            <td className="py-0.5 pr-2">
              {p.label}
              <span className="block text-[10px] text-muted">{p.score === null ? "insufficient data" : p.detail}</span>
            </td>
            <td className="text-right">{p.weight}%</td>
            <td className="text-right">{p.score ?? "—"}</td>
            <td className="text-right font-semibold">{p.points ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Prop Intelligence and (for shots props) Shot Environment, each with what produced it. */
export function ScoreDetails({ r }: { r: PropRow }) {
  const [why, setWhy] = useState(false);
  const s = r.scores;
  if (!s) return null;
  const env = [...s.shot_env_parts].filter((p) => p.score !== null).sort((a, b) => Math.abs(b.score! - 50) * b.weight - Math.abs(a.score! - 50) * a.weight);
  return (
    <section className="flex flex-col gap-2" aria-label="Prop scores">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
          Prop Intelligence {s.intelligence !== null ? `${s.intelligence}/100` : "· insufficient data"}
        </h3>
        <button type="button" onClick={() => setWhy(!why)} className="min-h-9 text-xs text-accent underline" aria-expanded={why}>
          {why ? "Hide" : "Why?"}
        </button>
      </div>
      {why && (
        <>
          <Parts parts={s.intelligence_parts} label="Prop Intelligence parts" />
          <p className="text-[10px] text-muted">
            Points = score × weight ÷ the weight of the parts with data. Weights are set in config/scoring.yml (version{" "}
            {s.config_version}), frozen with this prediction.
          </p>
        </>
      )}
      {s.shot_environment !== null && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Shot Environment: {s.shot_environment}/100</h3>
          <ul className="mt-1 text-[11px]" aria-label="Biggest shot environment factors">
            {env.slice(0, 3).map((p) => (
              <li key={p.part}>
                <span className={p.score! >= 50 ? "text-over" : "text-bad"}>{p.score! >= 50 ? "▲" : "▼"}</span> {p.label}: {p.detail}
              </li>
            ))}
          </ul>
          {why && <Parts parts={s.shot_env_parts} label="Shot Environment parts" />}
        </div>
      )}
    </section>
  );
}

/** Model confidence (trust in the number) beside prop value (the price), and why they differ. */
export function ConfidenceVsValue({ r }: { r: PropRow }) {
  const conf = r.confidence;
  const value = r.scores?.value ?? null;
  if (conf === null && value === null) return null;
  let note = "";
  if (conf !== null && value !== null) {
    if (conf - value >= 15)
      note = "The model's number is well supported, but the price leaves little expected value: the market roughly agrees.";
    else if (value - conf >= 15)
      note = "The price looks generous against the model, but the model's number carries more uncertainty (data, role or market disagreement): treat the value with care.";
    else note = "Trust in the model's number and the value in the price are about level.";
  }
  return (
    <section aria-label="Confidence vs value">
      <dl className="num grid grid-cols-2 gap-x-4 text-sm">
        <dt className="text-xs text-muted">Model confidence</dt>
        <dt className="text-xs text-muted">Prop value</dt>
        <dd className="font-semibold">{conf ?? "—"}/100</dd>
        <dd className="font-semibold">{value ?? "—"}/100</dd>
      </dl>
      <p className="mt-1 text-[11px] text-muted">
        Confidence is how far to trust the model's probability (edge vs its uncertainty, role, data, book agreement,
        availability). Value is the expected return at this price. {note}
      </p>
    </section>
  );
}

export function WhyThisProp({ r }: { r: PropRow }) {
  const s = r.scores;
  if (!s || s.why.length === 0) return null;
  return (
    <section aria-label="Why this prop">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Why this prop</h3>
      <ul className="num text-sm">
        {s.why.map((b) => (
          <li key={b.label} className="flex justify-between gap-2">
            <span className="text-muted">• {b.label}</span>
            <span>{b.value}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs">{s.summary}</p>
      <p className="mt-0.5 text-[10px] text-muted">Written from the inputs above only; nothing else is added.</p>
    </section>
  );
}
