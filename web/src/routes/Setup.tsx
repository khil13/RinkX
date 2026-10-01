import { useState, type FormEvent, type ReactNode } from "react";
import { Button, Notice, Panel } from "../components/ui";
import { generateSetup, type Keyfile } from "../lib/crypto";
import type { Manifest } from "../lib/data/types";
import { githubRepo } from "../lib/format";

const MIN_CHARS = 20;

interface Generated {
  dataKey: string;
  storeKey: string;
  keyfile: Keyfile;
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <div className="text-xs font-semibold text-muted">{label}</div>
      <div className="flex items-stretch gap-2">
        <code className="num min-w-0 flex-1 break-all rounded-md border border-line bg-bg px-2 py-2 text-xs">
          {value}
        </code>
        <Button
          variant="ghost"
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(value);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="flex flex-col gap-3 rounded-md border border-line p-3">
      <div className="text-sm font-semibold">
        <span className="num mr-2 text-accent">{n}.</span>
        {title}
      </div>
      {children}
    </li>
  );
}

export function Setup({ manifest }: { manifest: Manifest | undefined }) {
  const [p1, setP1] = useState("");
  const [p2, setP2] = useState("");
  const [busy, setBusy] = useState(false);
  const [gen, setGen] = useState<Generated | null>(null);
  const repo = githubRepo();
  const repoUrl = repo ? `https://github.com/${repo.owner}/${repo.repo}` : null;
  const configured = manifest?.configured ?? false;

  const problem =
    p1.length > 0 && p1.length < MIN_CHARS
      ? `Use at least ${MIN_CHARS} characters (five or more random words is ideal).`
      : p2.length > 0 && p1 !== p2
        ? "Passphrases don't match."
        : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      setGen(await generateSetup(p1));
    } finally {
      setBusy(false);
    }
  }

  const keyfileText = gen ? JSON.stringify(gen.keyfile, null, 2) + "\n" : "";

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4 sm:p-6">
      <div>
        <div className="text-2xl font-bold tracking-widest text-accent">RINKX · Setup</div>
        <p className="mt-1 text-sm text-muted">
          One-time setup. Keys are generated on this device and shown once. Nothing is sent anywhere.
        </p>
      </div>

      {configured ? (
        <Notice tone="warn">
          This site is already set up. Generating new keys and replacing <b>STORE_KEY</b> would make the existing
          data store unreadable. Only continue if you mean to start over.
        </Notice>
      ) : (
        manifest && (
          <Notice>
            Missing: <span className="num">{manifest.missing_setup.join(", ")}</span>
          </Notice>
        )
      )}

      {!gen ? (
        <Panel title="Choose a passphrase">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <p className="text-sm text-muted">
              Everything on this public site is encrypted with a key that only this passphrase unlocks. Anyone can
              download the encrypted files and try to guess it, so make it long, e.g. five or more random words. If
              you forget it, run setup again (the data store is unaffected as long as you keep STORE_KEY).
            </p>
            <input
              type="password"
              autoComplete="new-password"
              placeholder="Passphrase"
              value={p1}
              onChange={(e) => setP1(e.target.value)}
              className="min-h-11 rounded-md border border-line bg-bg px-3 text-base outline-none focus:border-accent"
            />
            <input
              type="password"
              autoComplete="new-password"
              placeholder="Repeat passphrase"
              value={p2}
              onChange={(e) => setP2(e.target.value)}
              className="min-h-11 rounded-md border border-line bg-bg px-3 text-base outline-none focus:border-accent"
            />
            {problem && <Notice tone="bad">{problem}</Notice>}
            <Button type="submit" disabled={busy || !!problem || p1.length < MIN_CHARS || p1 !== p2}>
              {busy ? "Generating… (takes a few seconds)" : "Generate keys"}
            </Button>
          </form>
        </Panel>
      ) : (
        <ol className="flex flex-col gap-3">
          <Step n={1} title="Add two repository secrets">
            <p className="text-sm text-muted">
              In GitHub: <b>Settings → Secrets and variables → Actions → New repository secret</b>. Add each one
              with exactly this name.
              {repoUrl && (
                <>
                  {" "}
                  <a className="text-accent underline" href={`${repoUrl}/settings/secrets/actions/new`}>
                    Open the page
                  </a>
                </>
              )}
            </p>
            <CopyField label="Name: DATA_KEY" value={gen.dataKey} />
            <CopyField label="Name: STORE_KEY" value={gen.storeKey} />
            <Notice tone="warn">
              Also save both keys somewhere safe outside GitHub (e.g. a password manager). GitHub won't show
              secrets again, and losing STORE_KEY means losing the data store.
            </Notice>
          </Step>
          <Step n={2} title="Commit the keyfile">
            <p className="text-sm text-muted">
              Create <code className="num">config/keyfile.json</code> with this content and commit it to{" "}
              <code className="num">main</code>. It's safe to publish: it only works with your passphrase.
            </p>
            {repoUrl && (
              <a
                className="text-sm text-accent underline"
                href={`${repoUrl}/new/main?filename=config/keyfile.json&value=${encodeURIComponent(keyfileText)}`}
              >
                Open GitHub with the file pre-filled
              </a>
            )}
            <CopyField label="config/keyfile.json" value={keyfileText} />
          </Step>
          <Step n={3} title="Run the pipeline">
            <p className="text-sm text-muted">
              In GitHub: <b>Actions → pipeline → Run workflow</b>. When it finishes, reload this page and unlock it
              with your passphrase.
              {repoUrl && (
                <>
                  {" "}
                  <a className="text-accent underline" href={`${repoUrl}/actions/workflows/pipeline.yml`}>
                    Open the workflow
                  </a>
                </>
              )}
            </p>
          </Step>
        </ol>
      )}
    </div>
  );
}
