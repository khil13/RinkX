"""Deterministic test vectors shared with the browser implementation.

Regenerate with `python -m rinkx.crypto_vectors > ../fixtures/crypto_vectors.json`.
pipeline/tests/test_crypto.py fails if the committed file drifts from this output, and
web/src/lib/crypto.test.ts decrypts the same file with WebCrypto.
"""

from __future__ import annotations

import json
from typing import Any

from rinkx import crypto

# Decomposed "é" (e + U+0301) on purpose: both sides must NFC-normalize before deriving.
PASSPHRASE = "correct horse battery staple café"
WRONG_PASSPHRASE = "correct horse battery staple cafe"
DATA_KEY = bytes(range(32))
SALT = bytes(range(100, 116))
IV = bytes(range(200, 212))
ITERATIONS = 1000  # low only so tests are fast; production keyfiles use 600k
PATH = "admin/health.json"
PAYLOAD: dict[str, Any] = {"data": {"hello": "rinkx", "n": 3, "unicode": "Šimon Nemec"}, "meta": {"v": 1}}


def build() -> dict[str, Any]:
    return {
        "passphrase": PASSPHRASE,
        "wrong_passphrase": WRONG_PASSPHRASE,
        "data_key_b64": crypto.b64e(DATA_KEY),
        "keyfile": crypto.make_keyfile(PASSPHRASE, DATA_KEY, iterations=ITERATIONS, salt=SALT),
        "file": {
            "path": PATH,
            "envelope": crypto.encrypt_json(PAYLOAD, PATH, DATA_KEY, iv=IV),
            "plaintext": PAYLOAD,
        },
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
