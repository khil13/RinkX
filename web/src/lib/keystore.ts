// Remembers the unlocked DATA_KEY on this device. The key is a non-extractable CryptoKey:
// IndexedDB can store it, but no script (including ours) can read its raw bytes back out.

const DB = "rinkx";
const STORE = "keys";
const ID = "data-key";

export interface StoredKey {
  key: CryptoKey;
  keyfileSha: string; // detects a re-keyed site so a stale key is discarded
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const req = fn(db.transaction(STORE, mode).objectStore(STORE));
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function loadKey(): Promise<StoredKey | null> {
  try {
    return ((await tx("readonly", (s) => s.get(ID))) as StoredKey | undefined) ?? null;
  } catch {
    return null; // private browsing or storage blocked: just ask for the passphrase
  }
}

export async function saveKey(value: StoredKey): Promise<void> {
  try {
    await tx("readwrite", (s) => s.put(value, ID));
  } catch {
    // Not fatal: the key stays in memory for this session.
  }
}

export async function clearKey(): Promise<void> {
  try {
    await tx("readwrite", (s) => s.delete(ID));
  } catch {
    // nothing stored
  }
}
