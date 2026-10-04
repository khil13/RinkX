import type { PropDecision, PropRow } from "../lib/data/types";
import { pct } from "./Projections";

const TONE: Record<PropDecision["code"], string> = {
  value: "border-over/50 text-over",
  lean: "border-accent/40 text-text",
  pass: "border-line text-muted",
  avoid: "border-bad/50 text-bad",
};

/** Bettable value / Lean / Pass / Avoid (pipeline/rinkx/publish/assess.py). */
export function DecisionBadge({ d }: { d: PropDecision | undefined }) {
  if (!d) return null;
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${TONE[d.code]}`}
      title={d.reason}
    >
      {d.label}
    </span>
  );
}

export function Decision({ r }: { r: PropRow }) {
  if (!r.decision) return null;
  return (
    <section aria-label="Decision" className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <DecisionBadge d={r.decision} />
        <span className="text-xs">{r.decision.reason}</span>
      </div>
      <p className="text-[11px] text-muted">
        Confidence ({r.confidence ?? "—"}/100) is how well the inputs support the number; value (EV) is what the price
        pays for it. Bettable value needs both, and no more than one risk below.
      </p>
    </section>
  );
}

const SPREAD: Record<string, string> = { low: "Tight", medium: "Moderate", high: "Wide" };

/** Floor / p25 / median / p75 / ceiling from the projection's own distribution, with the line. */
export function ProjectionRange({ r }: { r: PropRow }) {
  const g = r.range;
  if (!g) return null;
  const top = Math.max(g.ceiling, r.line ?? 0) + 1;
  const x = (v: number) => `${(v / top) * 100}%`;
  return (
    <section aria-label="Projection range">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">
        Projection range{g.spread ? ` · ${SPREAD[g.spread]} spread` : ""}
      </h3>
      <div className="relative h-6" aria-hidden>
        <div className="absolute top-1/2 h-px w-full bg-line" />
        <div className="absolute top-1/2 h-1 -translate-y-1/2 bg-accent/30" style={{ left: x(g.floor), width: x(g.ceiling - g.floor) }} />
        <div className="absolute top-1/2 h-3 -translate-y-1/2 rounded-sm bg-accent/60" style={{ left: x(g.p25), width: x(Math.max(g.p75 - g.p25, 0.05)) }} />
        <div className="absolute top-0 h-6 w-0.5 bg-text" style={{ left: x(g.median) }} />
        {r.line !== null && <div className="absolute top-0 h-6 w-0.5 bg-warn" style={{ left: x(r.line) }} />}
      </div>
      <dl className="num grid grid-cols-5 text-center text-[11px]">
        {(
          [
            ["Floor", g.floor],
            ["25th", g.p25],
            ["Median", g.median],
            ["75th", g.p75],
            ["Ceiling", g.ceiling],
          ] as const
        ).map(([k, v]) => (
          <div key={k}>
            <dt className="text-muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-1 text-[11px] text-muted">
        From the model's distribution: mean {g.mean.toFixed(2)}, sd {g.sd.toFixed(2)}. Floor and ceiling are the 10th
        and 90th percentiles{r.line !== null ? "; the yellow mark is the line" : ""}.
      </p>
    </section>
  );
}

export function WhyNot({ r }: { r: PropRow }) {
  if (!r.risks) return null;
  return (
    <section aria-label="Why not?">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Why not?</h3>
      {r.risks.length === 0 ? (
        <p className="text-xs text-muted">No risk flags from the data on hand. Props like this still lose often.</p>
      ) : (
        <ul className="flex flex-col gap-0.5 text-xs">
          {r.risks.map((k) => (
            <li key={k.code} className={k.serious ? "text-bad" : ""}>
              {k.serious ? "■ " : "• "}
              {k.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Model probability before and after the live calibrator, when one applied. */
export function RawVsCalibrated({ r }: { r: PropRow }) {
  if (!r.calibration) return null;
  return (
    <>
      <dt className="text-muted">Raw → calibrated</dt>
      <dd>
        {pct(r.calibration.raw)} → {pct(r.calibration.calibrated)}
      </dd>
    </>
  );
}
