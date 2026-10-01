import { describe, expect, it } from "vitest";
import vectors from "../../../fixtures/crypto_vectors.json";
import { b64decode, decryptJson, makeKeyfile, unlock, WrongPassphraseError } from "./crypto";

// These vectors are produced by the Python pipeline (rinkx.crypto_vectors), so passing here
// proves the browser can read exactly what the pipeline publishes.
describe("crypto interop with the Python pipeline", () => {
  it("unlocks the Python keyfile and decrypts the Python-encrypted file", async () => {
    const key = await unlock(vectors.keyfile, vectors.passphrase);
    const out = await decryptJson(vectors.file.envelope, vectors.file.path, key);
    expect(out).toEqual(vectors.file.plaintext);
  });

  it("rejects a wrong passphrase", async () => {
    await expect(unlock(vectors.keyfile, vectors.wrong_passphrase)).rejects.toBeInstanceOf(WrongPassphraseError);
  });

  it("normalizes the passphrase like Python (composed é unlocks a keyfile made with decomposed é)", async () => {
    const composed = vectors.passphrase.normalize("NFC");
    expect(composed).not.toBe(vectors.passphrase);
    await expect(unlock(vectors.keyfile, composed)).resolves.toBeTruthy();
  });

  it("refuses a file decrypted under the wrong path", async () => {
    const key = await unlock(vectors.keyfile, vectors.passphrase);
    await expect(decryptJson(vectors.file.envelope, "slate/other.json", key)).rejects.toThrow(/Could not decrypt/);
  });

  it("produces byte-identical keyfiles to Python for the same inputs", async () => {
    const kf = await makeKeyfile(
      vectors.passphrase,
      b64decode(vectors.data_key_b64),
      b64decode(vectors.keyfile.salt),
      vectors.keyfile.iterations,
    );
    expect(kf).toEqual(vectors.keyfile);
  });
});
