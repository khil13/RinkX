from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rinkx import crypto
from rinkx.config import REPO_ROOT, ConfigError, Settings
from rinkx.store import remote
from rinkx.store.db import MigrationError, connect, migrate, schema_version, snapshot

DB_DIR = REPO_ROOT / "db"


def test_migrate_fresh_db(tmp_path: Path):
    conn = connect(tmp_path / "a.db")
    assert migrate(conn, DB_DIR) == 1
    assert schema_version(conn) == 1
    assert conn.execute("SELECT count(*) FROM markets").fetchone()[0] == 19
    assert migrate(conn, DB_DIR) == 1  # idempotent


def test_migrate_refuses_unknown_db(tmp_path: Path):
    conn = connect(tmp_path / "a.db")
    conn.execute("CREATE TABLE stray (x INTEGER)")
    with pytest.raises(MigrationError):
        migrate(conn, DB_DIR)


def test_migrations_applied_in_order(tmp_path: Path):
    db_dir = tmp_path / "db"
    (db_dir / "migrations").mkdir(parents=True)
    (db_dir / "schema.sql").write_text((DB_DIR / "schema.sql").read_text())
    (db_dir / "migrations/0002_add_note.sql").write_text("ALTER TABLE teams ADD COLUMN note TEXT;")
    conn = connect(tmp_path / "a.db")
    assert migrate(conn, db_dir) == 2
    assert "note" in [r[1] for r in conn.execute("PRAGMA table_info(teams)")]
    (db_dir / "migrations/0004_gap.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError):
        migrate(conn, db_dir)


def test_push_pull_roundtrip_and_rotation(tmp_path: Path):
    backend = remote.LocalBackend(tmp_path / "remote")
    key = crypto.new_key()
    assert remote.pull(backend, key, tmp_path / "x.db") is None

    conn = connect(tmp_path / "live.db")
    migrate(conn, DB_DIR)
    conn.execute("INSERT INTO seasons VALUES (20262027, '2026-10-01', '2027-06-30')")
    conn.commit()
    snap = tmp_path / "snap.db"
    snapshot(conn, snap)

    t0 = datetime(2026, 10, 10, tzinfo=UTC)
    names = [remote.push(backend, key, snap, now=t0 + timedelta(minutes=i)) for i in range(12)]
    assets = backend.list_assets()
    assert len(assets) == remote.KEEP_VERSIONS
    assert assets[-1] == names[-1] and names[0] not in assets

    out = tmp_path / "pulled.db"
    assert remote.pull(backend, key, out) == names[-1]
    pulled = connect(out)
    assert pulled.execute("SELECT id FROM seasons").fetchone()[0] == 20262027
    assert out.read_bytes()[:16] == b"SQLite format 3\x00"
    raw = (tmp_path / "remote" / names[-1]).read_bytes()
    assert b"SQLite format" not in raw  # encrypted at rest


def test_pull_with_wrong_key_fails_instead_of_resetting(tmp_path: Path):
    backend = remote.LocalBackend(tmp_path / "remote")
    db = tmp_path / "a.db"
    migrate(connect(db), DB_DIR)
    remote.push(backend, crypto.new_key(), db)
    with pytest.raises(crypto.DecryptionError):
        remote.pull(backend, crypto.new_key(), tmp_path / "b.db")


def test_github_backend_requires_repo():
    s = Settings.from_env({"RINKX_ENV": "prod"})
    with pytest.raises(ConfigError):
        remote.backend_for(s)
