import json

import pytest

from rinkx import crypto, crypto_vectors
from rinkx.config import REPO_ROOT

VECTORS = REPO_ROOT / "fixtures/crypto_vectors.json"


def test_committed_vectors_match_implementation():
    assert json.loads(VECTORS.read_text()) == crypto_vectors.build()


def test_keyfile_roundtrip_and_wrong_passphrase():
    key = crypto.new_key()
    kf = crypto.make_keyfile("a long enough passphrase here", key, iterations=1000)
    assert crypto.unwrap_keyfile(kf, "a long enough passphrase here") == key
    assert crypto.keyfile_matches(kf, key)
    assert not crypto.keyfile_matches(kf, crypto.new_key())
    with pytest.raises(crypto.DecryptionError):
        crypto.unwrap_keyfile(kf, "a long enough passphrase herE")


def test_passphrase_is_nfc_normalized():
    key = crypto.new_key()
    kf = crypto.make_keyfile("café passphrase", key, iterations=1000)
    assert crypto.unwrap_keyfile(kf, "café passphrase") == key


def test_file_roundtrip_binds_path():
    key = crypto.new_key()
    env = crypto.encrypt_json({"x": 1}, "slate/2026-10-10.json", key)
    assert crypto.decrypt_json(env, "slate/2026-10-10.json", key) == {"x": 1}
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_json(env, "slate/2026-10-11.json", key)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_json(env, "slate/2026-10-10.json", crypto.new_key())


def test_fresh_iv_per_encryption():
    key = crypto.new_key()
    a = crypto.encrypt_json({"x": 1}, "p", key)
    b = crypto.encrypt_json({"x": 1}, "p", key)
    assert a["iv"] != b["iv"] and a["ct"] != b["ct"]


def test_store_roundtrip_and_tamper_detection():
    key = crypto.new_key()
    blob = crypto.encrypt_store(b"SQLite format 3\x00...", key)
    assert crypto.decrypt_store(blob, key) == b"SQLite format 3\x00..."
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_store(tampered, key)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_store(blob, crypto.new_key())
