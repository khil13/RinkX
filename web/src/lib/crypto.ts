// Browser side of pipeline/rinkx/crypto.py. Formats are pinned by fixtures/crypto_vectors.json.
//
// keyfile.json (public): DATA_KEY wrapped with AES-KW under PBKDF2-SHA256(passphrase).
// *.json.enc:            AES-256-GCM, additional data = "rinkx:" + logical path (without ".enc").

export interface Keyfile {
  v: number;
  kdf: string;
  iterations: number;
  salt: string;
  wrapped_key: string;
  key_check: string;
}

export interface EncryptedFile {
  v: number;
  alg: string;
  iv: string;
  ct: string;
}

export class WrongPassphraseError extends Error {
  constructor() {
    super("Wrong passphrase");
    this.name = "WrongPassphraseError";
  }
}

export class DecryptError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DecryptError";
  }
}

export const DEFAULT_ITERATIONS = 600_000;
const enc = new TextEncoder();

export function b64decode(text: string): Uint8Array<ArrayBuffer> {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function b64encode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function passphraseBytes(passphrase: string): Uint8Array<ArrayBuffer> {
  // NFC, matching Python's unicodedata.normalize("NFC", ...).
  return enc.encode(passphrase.normalize("NFC"));
}

async function deriveKek(passphrase: string, salt: Uint8Array<ArrayBuffer>, iterations: number, usages: KeyUsage[]) {
  const base = await crypto.subtle.importKey("raw", passphraseBytes(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    base,
    { name: "AES-KW", length: 256 },
    false,
    usages,
  );
}

/** Recover DATA_KEY as a non-extractable AES-GCM decryption key. Throws WrongPassphraseError. */
export async function unlock(keyfile: Keyfile, passphrase: string): Promise<CryptoKey> {
  if (keyfile.v !== 1 || keyfile.kdf !== "PBKDF2-SHA256") throw new DecryptError("Unsupported keyfile format");
  const kek = await deriveKek(passphrase, b64decode(keyfile.salt), keyfile.iterations, ["unwrapKey"]);
  try {
    return await crypto.subtle.unwrapKey(
      "raw",
      b64decode(keyfile.wrapped_key),
      kek,
      "AES-KW",
      { name: "AES-GCM" },
      false,
      ["decrypt"],
    );
  } catch {
    // AES-KW has a built-in integrity check, so a wrong passphrase always fails here.
    throw new WrongPassphraseError();
  }
}

export async function decryptJson<T>(file: EncryptedFile, path: string, key: CryptoKey): Promise<T> {
  if (file.v !== 1 || file.alg !== "AES-256-GCM") throw new DecryptError("Unsupported file format");
  let plain: ArrayBuffer;
  try {
    plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64decode(file.iv), additionalData: enc.encode(`rinkx:${path}`) },
      key,
      b64decode(file.ct),
    );
  } catch {
    throw new DecryptError(`Could not decrypt ${path} (key changed or file modified)`);
  }
  return JSON.parse(new TextDecoder().decode(plain)) as T;
}

/** First-time setup, done entirely in the browser: random keys + keyfile for the passphrase. */
export async function generateSetup(passphrase: string, iterations = DEFAULT_ITERATIONS) {
  const dataKey = crypto.getRandomValues(new Uint8Array(32));
  const storeKey = crypto.getRandomValues(new Uint8Array(32));
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const keyfile = await makeKeyfile(passphrase, dataKey, salt, iterations);
  return { dataKey: b64encode(dataKey), storeKey: b64encode(storeKey), keyfile };
}

export async function makeKeyfile(
  passphrase: string,
  dataKey: Uint8Array<ArrayBuffer>,
  salt: Uint8Array<ArrayBuffer>,
  iterations: number,
): Promise<Keyfile> {
  const kek = await deriveKek(passphrase, salt, iterations, ["wrapKey"]);
  const raw = await crypto.subtle.importKey("raw", dataKey, { name: "AES-GCM" }, true, ["encrypt"]);
  const wrapped = new Uint8Array(await crypto.subtle.wrapKey("raw", raw, kek, "AES-KW"));
  const hmacKey = await crypto.subtle.importKey("raw", dataKey, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const check = new Uint8Array(await crypto.subtle.sign("HMAC", hmacKey, enc.encode("rinkx-key-check")));
  return {
    v: 1,
    kdf: "PBKDF2-SHA256",
    iterations,
    salt: b64encode(salt),
    wrapped_key: b64encode(wrapped),
    key_check: b64encode(check.slice(0, 16)),
  };
}
