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
    assert set(m["files"]) == {"keyfile.json", "admin/health.json.enc", "models.json.enc"}
    assert result.store_pulled is None and result.store_pushed is not None

    # Every expected feed is reported, and with no data sources connected all are unavailable.
    assert [f["code"] for f in m["feeds"]] == ["schedule", "stats", "lineups", "goalies", "injuries", "odds", "news"]
    assert {f["state"] for f in m["feeds"]} == {"unavailable"}

    # The passphrase recovers DATA_KEY from the published keyfile, which decrypts the files.
    keyfile = json.loads((out / "keyfile.json").read_text())
    data_key = crypto.unwrap_keyfile(keyfile, PASSPHRASE)
    env = crypto.decrypt_json(json.loads((out / "admin/health.json.enc").read_text()), "admin/health.json", data_key)
    assert env["meta"]["data_status"] == "live"
    assert env["data"]["store"]["schema_version"] >= 2
    assert "Auston" not in (out / "admin/health.json.enc").read_text()
    models = crypto.decrypt_json(json.loads((out / "models.json.enc").read_text()), "models.json", data_key)
    assert models["data"]["status"] == "insufficient_history"  # no games: nothing tested, nothing projected
    assert not any(e["passed"] for e in models["data"]["stats"].values())

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


def test_publishes_league_files_from_replayed_nhl_data(tmp_path: Path, keys):
    from dataclasses import replace

    from rinkx.config import REPO_ROOT

    settings = replace(
        make_settings(tmp_path, keys, env="prod"),
        sources=frozenset({"nhl"}),
        fixtures_dir=REPO_ROOT / "pipeline/tests/fixtures/nhl",
        today=datetime(2026, 3, 10).date(),
        boxscore_limit=100,
    )
    out = tmp_path / "data"
    run(settings, out)  # real clock: ingestion timestamps are real, so feeds are fresh
    m = read_manifest(out)
    assert m["slate_date"] == "2026-03-10"
    assert {f["code"]: f["state"] for f in m["feeds"]}["schedule"] == "ok"
    files = set(m["files"])
    assert {"slate/2026-03-10.json.enc", "teams.json.enc", "players/index.json.enc"} <= files
    assert "games/2025021012.json.enc" in files
    # Only dates a successful schedule fetch covered get a slate. The recorded week starts on
    # 03-10, so 03-07..03-09 must be absent (unknown), not published as "no games".
    slates = sorted(f for f in files if f.startswith("slate/"))
    assert slates == [f"slate/2026-03-{d}.json.enc" for d in (10, 11, 12, 13)]

    key = keys["data_key"]

    def read(rel: str) -> dict:
        return crypto.decrypt_json(json.loads((out / f"{rel}.enc").read_text()), rel, key)

    slate = read("slate/2026-03-10.json")
    assert slate["meta"]["sources"] == ["nhl_stats_api", "nhl_web_boxscore", "nhl_web_schedule"]
    games = slate["data"]["games"]
    assert len(games) == 13
    bos = next(g for g in games if g["id"] == 2025021012)
    assert (bos["home"]["team"]["abbrev"], bos["home"]["score"], bos["away"]["score"]) == ("BOS", 2, 1)
    assert bos["home"]["record"]["as_of"] == "2026-03-10"
    assert bos["home"]["goalie"]["name"] == "Jeremy Swayman" and bos["home"]["goalie"]["status"] == "actual"
    assert bos["environment"] is None and bos["environment_reason"] == "not_connected"

    game = read("games/2025021012.json")["data"]
    assert game["boxscore"]["goalies"][0]["name"] in ("Joonas Korpisalo", "Jeremy Swayman", "Darcy Kuemper")
    assert sum(s["g"] for s in game["boxscore"]["skaters"]) == 3
    assert game["home"]["metrics"] is None and game["home"]["metrics_reason"] == "insufficient_sample"

    other = next(g for g in games if g["id"] != 2025021012)
    assert read(f"games/{other['id']}.json")["data"]["boxscore_reason"] == "data_unavailable"

    swayman = read("players/8480280.json")["data"]
    assert swayman["player"]["name"] == "Jeremy Swayman"
    assert swayman["totals"]["w"] == 1 and swayman["games"][0]["sv"] == 15
