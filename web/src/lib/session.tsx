import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { unlock } from "./crypto";
import { fetchKeyfile } from "./data/fetch";
import type { Manifest } from "./data/types";
import { clearKey, loadKey, saveKey } from "./keystore";

interface Session {
  key: CryptoKey | null;
  restoring: boolean;
  unlockWith: (passphrase: string, remember: boolean) => Promise<void>;
  lock: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);

export function SessionProvider({ manifest, children }: { manifest: Manifest | undefined; children: ReactNode }) {
  const [key, setKey] = useState<CryptoKey | null>(null);
  const [restoring, setRestoring] = useState(true);
  const keyfileSha = manifest?.files["keyfile.json"]?.sha256;

  // Restore a remembered key, but only if the site has not been re-keyed since.
  useEffect(() => {
    if (!manifest) return;
    let cancelled = false;
    (async () => {
      const stored = await loadKey();
      if (cancelled) return;
      if (stored && stored.keyfileSha === keyfileSha) setKey(stored.key);
      else if (stored) await clearKey();
      setRestoring(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [manifest, keyfileSha]);

  const unlockWith = useCallback(
    async (passphrase: string, remember: boolean) => {
      if (!manifest || !keyfileSha) throw new Error("Site is not set up yet");
      const k = await unlock(await fetchKeyfile(manifest), passphrase);
      if (remember) await saveKey({ key: k, keyfileSha });
      setKey(k);
    },
    [manifest, keyfileSha],
  );

  const lock = useCallback(async () => {
    await clearKey();
    setKey(null);
  }, []);

  const value = useMemo(() => ({ key, restoring, unlockWith, lock }), [key, restoring, unlockWith, lock]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}
