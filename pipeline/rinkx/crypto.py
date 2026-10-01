"""Encryption for the data store and the published bundle.

Two independent 256-bit keys, both held only in Actions secrets:

* DATA_KEY  encrypts every published `*.json.enc` file (AES-256-GCM). The public
            `keyfile.json` carries DATA_KEY wrapped (AES-KW, RFC 3394) under a key derived
            from the owner's passphrase (PBKDF2-SHA256), so the browser can recover it.
* STORE_KEY encrypts the SQLite store uploaded as a Release asset (AES-256-GCM).

The formats are mirrored by web/src/lib/crypto.ts; fixtures/crypto_vectors.json pins
them so the Python and browser implementations cannot drift apart.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import unicodedata
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap, aes_key_wrap

KEYFILE_VERSION = 1
FILE_VERSION = 1
DEFAULT_ITERATIONS = 600_000
STORE_MAGIC = b"RINKXDB1"
STORE_AAD = b"rinkx-store-v1"
KEY_CHECK_INFO = b"rinkx-key-check"


class DecryptionError(Exception):
    """Wrong key/passphrase, or the ciphertext was modified."""


def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


def new_key() -> bytes:
    return os.urandom(32)


def normalize_passphrase(passphrase: str) -> bytes:
    # NFC so that composed/decomposed characters typed on different devices derive the same key.
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


def derive_kek(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(normalize_passphrase(passphrase))


def key_check(data_key: bytes) -> str:
    """Short fingerprint letting the pipeline confirm DATA_KEY matches the published keyfile."""
    return b64e(hmac.new(data_key, KEY_CHECK_INFO, hashlib.sha256).digest()[:16])


def make_keyfile(
    passphrase: str,
    data_key: bytes,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    salt: bytes | None = None,
) -> dict[str, Any]:
    salt = salt if salt is not None else os.urandom(16)
    kek = derive_kek(passphrase, salt, iterations)
    return {
        "v": KEYFILE_VERSION,
        "kdf": "PBKDF2-SHA256",
        "iterations": iterations,
        "salt": b64e(salt),
        "wrapped_key": b64e(aes_key_wrap(kek, data_key)),
        "key_check": key_check(data_key),
    }


def unwrap_keyfile(keyfile: dict[str, Any], passphrase: str) -> bytes:
    if keyfile.get("v") != KEYFILE_VERSION or keyfile.get("kdf") != "PBKDF2-SHA256":
        raise DecryptionError("unsupported keyfile format")
    kek = derive_kek(passphrase, b64d(keyfile["salt"]), int(keyfile["iterations"]))
    try:
        return aes_key_unwrap(kek, b64d(keyfile["wrapped_key"]))
    except InvalidUnwrap as exc:
        raise DecryptionError("wrong passphrase") from exc


def keyfile_matches(keyfile: dict[str, Any], data_key: bytes) -> bool:
    return hmac.compare_digest(str(keyfile.get("key_check", "")), key_check(data_key))


def file_aad(path: str) -> bytes:
    # Binding the published path means one encrypted file can't be served in place of another.
    return b"rinkx:" + path.encode("utf-8")


def encrypt_json(obj: Any, path: str, data_key: bytes, *, iv: bytes | None = None) -> dict[str, Any]:
    iv = iv if iv is not None else os.urandom(12)
    plaintext = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ct = AESGCM(data_key).encrypt(iv, plaintext, file_aad(path))
    return {"v": FILE_VERSION, "alg": "AES-256-GCM", "iv": b64e(iv), "ct": b64e(ct)}


def decrypt_json(envelope: dict[str, Any], path: str, data_key: bytes) -> Any:
    if envelope.get("v") != FILE_VERSION or envelope.get("alg") != "AES-256-GCM":
        raise DecryptionError("unsupported file format")
    try:
        pt = AESGCM(data_key).decrypt(b64d(envelope["iv"]), b64d(envelope["ct"]), file_aad(path))
    except InvalidTag as exc:
        raise DecryptionError("wrong key or tampered file") from exc
    return json.loads(pt)


def encrypt_store(plaintext: bytes, store_key: bytes) -> bytes:
    nonce = os.urandom(12)
    return STORE_MAGIC + nonce + AESGCM(store_key).encrypt(nonce, plaintext, STORE_AAD)


def decrypt_store(blob: bytes, store_key: bytes) -> bytes:
    if not blob.startswith(STORE_MAGIC):
        raise DecryptionError("not a RinkX store file")
    nonce = blob[len(STORE_MAGIC) : len(STORE_MAGIC) + 12]
    try:
        return AESGCM(store_key).decrypt(nonce, blob[len(STORE_MAGIC) + 12 :], STORE_AAD)
    except InvalidTag as exc:
        raise DecryptionError("wrong STORE_KEY or corrupted store") from exc
