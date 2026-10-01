import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import PASSPHRASE, make_settings

from rinkx import crypto
from rinkx.config import ConfigError
from rinkx.pipeline import run
from rinkx.publish.guards import PublishBlocked
from rinkx.store import remote
from rinkx.store.db import connect

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)


def read_manifest(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text())


def test_unconfigured_publishes_setup_only_site(tmp_path: Path):
    out = tmp_path / "data"
    result = run(make_settings(tmp_path, None), out, now=NOW)
    m = read_manifest(out)
    assert m["configured"] is False
    assert set(m["missing_setup"]) == {"keyfile", "DATA_KEY", "STORE_KEY"}
    assert m["files"] == {}
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json"]
    assert result.store_pushed is None


def test_configured_run_publishes_encrypted_bundle(tmp_path: Path, keys):
    out = tmp_path / "data"
    result = run(make_settings(tmp_path, keys), out, now=NOW)
    m = read_manifest(out)
    assert m["configured"] is True and m["missing_setup"] == []
    assert set(m["files"]) == {"keyfile.json", "admin/health.json.enc"}
    assert result.store_pulled is None and result.store_pushed is not None

    # Every expected feed is reported, and with no data sources connected all are unavailable.
    assert [f["code"] for f in m["feeds"]] == ["schedule", "stats", "lineups", "goalies", "injuries", "odds", "news"]
    assert {f["state"] for f in m["feeds"]} == {"unavailable"}

    # The passphrase recovers DATA_KEY from the published keyfile, which decrypts the files.
    keyfile = json.loads((out / "keyfile.json").read_text())
    data_key = crypto.unwrap_keyfile(keyfile, PASSPHRASE)
    env = crypto.decrypt_json(json.loads((out / "admin/health.json.enc").read_text()), "admin/health.json", data_key)
    assert env["meta"]["data_status"] == "live"
    assert env["data"]["store"]["schema_version"] == 1
    assert "Auston" not in (out / "admin/health.json.enc").read_text()

    # Second run pulls what the first pushed.
    again = run(make_settings(tmp_path, keys), tmp_path / "data", now=NOW)
    assert again.store_pulled == result.store_pushed


def test_manifest_hashes_match_files(tmp_path: Path, keys):
    import hashlib

    out = tmp_path / "data"
    run(make_settings(tmp_path, keys), out, now=NOW)
    for rel, entry in read_manifest(out)["files"].items():
        raw = (out / rel).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"] and len(raw) == entry["bytes"]


def test_mismatched_data_key_is_rejected(tmp_path: Path, keys):
    keys = dict(keys, data_key=crypto.new_key())
    with pytest.raises(ConfigError, match="does not match"):
        run(make_settings(tmp_path, keys), tmp_path / "data", now=NOW)


def _seed_synthetic(tmp_path: Path, keys) -> None:
    """Put a synthetic row into the stored DB, as a dev fixture would."""
    settings = make_settings(tmp_path, keys)
    run(settings, tmp_path / "data", now=NOW)
    backend = remote.backend_for(settings)
    db = tmp_path / "seed.db"
    remote.pull(backend, keys["store_key"], db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO data_sources (code, name, category, tier, license_notes) "
        "VALUES ('synthetic_dev', 'Synthetic', 'stats', 'free', 'test only')"
    )
    conn.execute(
        "INSERT INTO news (source_id, url, headline, category, reliability, published_at, fetched_at, provenance) "
        "VALUES (1, 'https://example.invalid/n', 'Synthetic headline', 'general', 'unverified', "
        "'2026-10-10T00:00:00Z', '2026-10-10T00:00:00Z', 'synthetic')"
    )
    conn.commit()
    conn.close()
    remote.push(backend, keys["store_key"], db)


def test_prod_publish_blocked_by_synthetic_rows(tmp_path: Path, keys):
    _seed_synthetic(tmp_path, keys)
    with pytest.raises(PublishBlocked, match="news=1"):
        run(make_settings(tmp_path, keys, env="prod"), tmp_path / "prod-data", now=NOW)
    assert not (tmp_path / "prod-data").exists()


def test_dev_publish_labels_synthetic(tmp_path: Path, keys):
    _seed_synthetic(tmp_path, keys)
    out = tmp_path / "data"
    run(make_settings(tmp_path, keys), out, now=NOW)
    data_key = keys["data_key"]
    env = crypto.decrypt_json(json.loads((out / "admin/health.json.enc").read_text()), "admin/health.json", data_key)
    assert env["meta"]["data_status"] == "synthetic"
    assert env["data"]["synthetic_rows"] == {"news": 1}


def test_refuses_to_overwrite_foreign_directory(tmp_path: Path, keys):
    out = tmp_path / "data"
    out.mkdir()
    (out / "important.txt").write_text("not ours")
    with pytest.raises(ConfigError, match="refusing"):
        run(make_settings(tmp_path, keys), out, now=NOW)
    assert (out / "important.txt").exists()
