import { useState, type FormEvent } from "react";
import { Button, Notice } from "../components/ui";
import { WrongPassphraseError } from "../lib/crypto";
import { useSession } from "../lib/session";

export function Unlock() {
  const { unlockWith } = useSession();
  const [passphrase, setPassphrase] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unlockWith(passphrase, remember);
    } catch (err) {
      setError(
        err instanceof WrongPassphraseError
          ? "That passphrase doesn't unlock this site."
          : `Could not unlock: ${(err as Error).message}`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-full max-w-sm flex-col justify-center gap-6 p-6">
      <div>
        <div className="text-2xl font-bold tracking-widest text-accent">RINKX</div>
        <p className="mt-1 text-sm text-muted">This site's data is encrypted. Enter your passphrase to read it.</p>
      </div>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5 text-sm">
          Passphrase
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            className="min-h-11 rounded-md border border-line bg-panel px-3 text-base outline-none focus:border-accent"
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember on this device
        </label>
        {error && <Notice tone="bad">{error}</Notice>}
        <Button type="submit" disabled={busy || passphrase.length === 0}>
          {busy ? "Unlocking…" : "Unlock"}
        </Button>
      </form>
      <p className="text-xs text-muted">
        Decryption happens on this device. Your passphrase is never sent anywhere.
      </p>
    </main>
  );
}
