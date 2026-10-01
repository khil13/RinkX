import type { ReactNode } from "react";
import type { FeedState } from "../lib/data/types";

export function Panel({ title, right, children }: { title?: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      {title && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const CHIP_STYLES: Record<string, string> = {
  ok: "border-over/40 text-over",
  fresh: "border-over/40 text-over",
  live: "border-over/40 text-over",
  stale: "border-warn/40 text-warn",
  failed: "border-bad/40 text-bad",
  unavailable: "border-line text-muted",
  synthetic: "border-warn bg-warn/10 text-warn",
};

// Color is never the only signal: every chip carries a text label.
const CHIP_LABELS: Record<string, string> = {
  ok: "LIVE",
  fresh: "UPDATED",
  live: "LIVE",
  stale: "STALE",
  failed: "FAILED",
  unavailable: "UNAVAILABLE",
  synthetic: "SYNTHETIC",
};

export function DataChip({ state, detail }: { state: FeedState | "live" | "fresh" | "synthetic"; detail?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-semibold tracking-wide ${CHIP_STYLES[state]}`}
    >
      <span aria-hidden>●</span>
      {CHIP_LABELS[state]}
      {detail && <span className="num font-normal text-muted">· {detail}</span>}
    </span>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "bad"; children: ReactNode }) {
  const styles = {
    info: "border-line bg-panel-2 text-muted",
    warn: "border-warn/50 bg-warn/10 text-warn",
    bad: "border-bad/50 bg-bad/10 text-bad",
  }[tone];
  return <div className={`rounded-md border px-3 py-2 text-sm ${styles}`}>{children}</div>;
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-3 p-10 text-sm text-muted" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}
    </div>
  );
}

export function Button({
  children,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  const styles =
    variant === "primary"
      ? "bg-accent text-bg hover:bg-accent/90 disabled:opacity-50"
      : "border border-line text-text hover:bg-panel-2";
  return (
    <button
      {...props}
      className={`min-h-11 rounded-md px-4 text-sm font-semibold transition-colors ${styles} ${props.className ?? ""}`}
    >
      {children}
    </button>
  );
}
