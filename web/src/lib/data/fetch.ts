import { useQuery } from "@tanstack/react-query";
import { decryptJson, type EncryptedFile, type Keyfile } from "../crypto";
import { useSession } from "../session";
import type { Envelope, Manifest } from "./types";

export const DATA_BASE = "data/";
export const MANIFEST_POLL_MS = 2 * 60 * 1000;

export async function fetchJson<T>(path: string, version?: string): Promise<T> {
  const url = DATA_BASE + path + (version ? `?v=${version.slice(0, 16)}` : "");
  const res = await fetch(url, { cache: version ? "default" : "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** The public manifest, re-checked every 2 minutes while the page is visible. */
export function useManifest() {
  return useQuery({
    queryKey: ["manifest"],
    queryFn: () => fetchJson<Manifest>("manifest.json"),
    refetchInterval: MANIFEST_POLL_MS,
    refetchIntervalInBackground: false,
  });
}

export function fetchKeyfile(manifest: Manifest) {
  return fetchJson<Keyfile>("keyfile.json", manifest.files["keyfile.json"]?.sha256);
}

export type EncryptedResult<T> =
  | { state: "loading" }
  | { state: "unavailable" } // not in the manifest: the pipeline has no data for it
  | { state: "error"; error: Error }
  | { state: "ready"; value: Envelope<T> };

/**
 * Fetch and decrypt `<path>.enc`. The query key includes the file's hash from the manifest,
 * so a file is refetched exactly when the pipeline publishes a new version of it.
 */
export function useEncrypted<T>(path: string): EncryptedResult<T> {
  const manifest = useManifest().data;
  const { key } = useSession();
  const entry = manifest?.files[`${path}.enc`];
  const q = useQuery({
    queryKey: ["enc", path, entry?.sha256],
    enabled: Boolean(entry && key),
    queryFn: async () => {
      const file = await fetchJson<EncryptedFile>(`${path}.enc`, entry!.sha256);
      return decryptJson<Envelope<T>>(file, path, key!);
    },
    staleTime: Infinity,
  });
  if (!manifest || (entry && q.isPending)) return { state: "loading" };
  if (!entry) return { state: "unavailable" };
  if (q.isError) return { state: "error", error: q.error };
  return q.data ? { state: "ready", value: q.data } : { state: "loading" };
}
