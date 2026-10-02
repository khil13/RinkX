import { Notice, Panel } from "../components/ui";
import { localTime } from "../lib/format";
import { coolOffActive, odds, type Settings as S, updateSettings, useSettings } from "../lib/settings";

const ZONES: [S["timeZone"], string][] = [
  ["", "This device's time zone"],
  ["America/New_York", "Eastern"],
  ["America/Chicago", "Central"],
  ["America/Denver", "Mountain"],
  ["America/Los_Angeles", "Pacific"],
  ["UTC", "UTC"],
];

const COOL_OFF: [number, string][] = [
  [24, "24 hours"],
  [72, "3 days"],
  [24 * 7, "1 week"],
  [24 * 30, "30 days"],
];

function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="mb-1 text-sm font-semibold">{label}</legend>
      {options.map(([v, l]) => (
        <label key={v || "device"} className="flex min-h-11 items-center gap-3 rounded-md px-2 hover:bg-panel-2">
          <input type="radio" name={label} checked={value === v} onChange={() => onChange(v)} className="size-4" />
          <span className="text-sm">{l}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function Settings() {
  const s = useSettings();
  const cooling = coolOffActive(s);
  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="text-sm text-muted">Stored on this device only. Each device keeps its own.</p>
      </header>

      <Panel title="Display">
        <div className="flex flex-col gap-4">
          <Choice
            label="Odds format"
            value={s.odds}
            options={[
              ["american", `American (${odds(-110, "american")}, ${odds(150, "american")})`],
              ["decimal", `Decimal (${odds(-110, "decimal")}, ${odds(150, "decimal")})`],
            ]}
            onChange={(v) => updateSettings({ odds: v })}
          />
          <Choice label="Times shown in" value={s.timeZone} options={ZONES} onChange={(v) => updateSettings({ timeZone: v })} />
        </div>
      </Panel>

      <Panel title="Cool-off">
        {cooling ? (
          <Notice tone="warn">
            Cool-off is on until {localTime(s.coolOffUntil!)}. Props, prices and the parlay builder are hidden on this
            device until then. It can be extended but not ended early from here.
          </Notice>
        ) : (
          <p className="mb-3 text-sm text-muted">
            Take a break: hide every price, prop and parlay on this device for a while. Stats, games and model tests
            stay available. Once started, it can't be ended early from the app.
          </p>
        )}
        <div className="mt-3 grid grid-cols-2 gap-2">
          {COOL_OFF.map(([h, label]) => (
            <button
              key={h}
              type="button"
              onClick={() => {
                const until = new Date(Date.now() + h * 3600_000).toISOString();
                if (window.confirm(`Hide props and prices on this device for ${label}? This can't be undone early.`)) {
                  updateSettings({ coolOffUntil: until });
                }
              }}
              className="min-h-11 rounded-md border border-line px-3 text-sm hover:border-accent/50"
            >
              {cooling ? `Extend: ${label}` : label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-[11px] text-muted">
          Push alerts come from the repository, not this device: switch them off in <code>config/alerts.yml</code>.
          Gambling problem? Call 1-800-GAMBLER.
        </p>
      </Panel>
    </div>
  );
}

/** Wraps pages that show prices; during a cool-off they show this instead. */
export function CoolOffGate({ children }: { children: React.ReactNode }) {
  const s = useSettings();
  if (!coolOffActive(s)) return <>{children}</>;
  return (
    <div className="mx-auto max-w-xl">
      <Notice tone="warn">
        Cool-off is on until {localTime(s.coolOffUntil!)}: props, prices and parlays are hidden on this device. Stats,
        games and model tests are still available.
      </Notice>
    </div>
  );
}
