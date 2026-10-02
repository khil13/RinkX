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
    latest = 1 + len(list((DB_DIR / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")))
    assert migrate(conn, DB_DIR) == latest
    assert schema_version(conn) == latest
    assert conn.execute("SELECT count(*) FROM markets").fetchone()[0] == 19
    assert migrate(conn, DB_DIR) == latest  # idempotent


def test_migrate_refuses_unknown_db(tmp_path: Path):
    conn = connect(tmp_path / "a.db")
    conn.execute("CREATE TABLE stray (x INTEGER)")
    with pytest.raises(MigrationError):
        migrate(conn, DB_DIR)


def test_migrations_applied_in_order(tmp_path: Path):
    db_dir = tmp_path / "db"
    (db_dir / "migrations").mkdir(parents=True)
    (db_dir / "schema.sql").write_text((DB_DIR / "schema.sql").read_text())
    # Exercise numbering with a private migration set (independent of the repo's migrations).
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


def test_repo_root_is_found_from_the_checkout_not_the_install_location(tmp_path: Path, monkeypatch):
    from rinkx.config import ConfigError, find_repo_root

    # Running from anywhere inside the checkout finds it (the CI/production case, where the
    # package is installed into site-packages and __file__ points far away).
    monkeypatch.chdir(REPO_ROOT / "web")
    assert find_repo_root(environ={}) == REPO_ROOT
    assert find_repo_root(start=REPO_ROOT / "pipeline/rinkx", environ={}) == REPO_ROOT
    assert find_repo_root(environ={"RINKX_REPO_ROOT": str(tmp_path)}) == tmp_path.resolve()
    # Outside any checkout, with the file-location fallback also outside: a clear error.
    import rinkx.config as cfg

    monkeypatch.setattr(cfg, "__file__", str(tmp_path / "site-packages/rinkx/config.py"))
    with pytest.raises(ConfigError, match="RINKX_REPO_ROOT"):
        find_repo_root(start=tmp_path, environ={})


def _db_with(tmp_path: Path, name: str, season: int) -> Path:
    db = tmp_path / name
    conn = connect(db)
    migrate(conn, DB_DIR)
    conn.execute("INSERT INTO seasons VALUES (?, '2026-10-01', '2027-06-30')", (season,))
    conn.commit()
    conn.close()
    return db


def test_rotation_keeps_recent_versions_and_one_per_week():
    t0 = datetime(2026, 8, 3, tzinfo=UTC)  # a Monday
    # Hourly for 10 weeks: 1,680 versions.
    names = [f"rinkx-{(t0 + timedelta(hours=h)).strftime('%Y%m%dT%H%M%S%fZ')}.db.enc" for h in range(24 * 70)]
    now = t0 + timedelta(hours=24 * 70)
    keep = remote.retained(names, now)
    recent = set(names[-remote.KEEP_VERSIONS :])
    weekly = keep - recent
    assert recent <= keep
    # One per ISO week, the newest in each, for the last 8 weeks (the newest week is already "recent").
    weeks = {remote.stamp_of(n).isocalendar()[:2] for n in weekly}
    assert len(weekly) == len(weeks) == remote.WEEKLY_SNAPSHOTS - 1
    for n in weekly:
        same_week = [m for m in names if remote.stamp_of(m).isocalendar()[:2] == remote.stamp_of(n).isocalendar()[:2]]
        assert n == max(same_week)
    assert min(remote.stamp_of(n) for n in keep) >= now - timedelta(weeks=remote.WEEKLY_SNAPSHOTS)


def test_restore_an_older_version_and_the_drill(tmp_path: Path):
    backend = remote.LocalBackend(tmp_path / "remote")
    key = crypto.new_key()
    t0 = datetime(2026, 10, 10, tzinfo=UTC)
    old = remote.push(backend, key, _db_with(tmp_path, "old.db", 20252026), now=t0)
    remote.push(backend, key, _db_with(tmp_path, "new.db", 20262027), now=t0 + timedelta(days=1))
    work = tmp_path / "work"
    work.mkdir()

    # The drill restores the OLDEST kept version, read-only.
    before = backend.list_assets()
    d = remote.drill(backend, key, work, DB_DIR, t0 + timedelta(days=2))
    assert d.ok and d.version == old and d.age_days == pytest.approx(2.0)
    assert d.detail["integrity"] == "ok" and d.detail["schema"] == schema_version(connect(tmp_path / "new.db"))
    assert backend.list_assets() == before and not list(work.iterdir())  # nothing uploaded, nothing left behind

    # Restoring makes the old contents current again, as a new version; history is kept.
    name = remote.restore(backend, key, old, work, now=t0 + timedelta(days=3))
    assert backend.list_assets()[-1] == name and old in backend.list_assets()
    out = tmp_path / "pulled.db"
    assert remote.pull(backend, key, out) == name
    assert connect(out).execute("SELECT id FROM seasons").fetchone()[0] == 20252026
    with pytest.raises(remote.StoreError, match="no stored version"):
        remote.restore(backend, key, "rinkx-nope.db.enc", work)


def test_drill_reports_failure_without_raising(tmp_path: Path):
    backend = remote.LocalBackend(tmp_path / "remote")
    key = crypto.new_key()
    remote.push(backend, key, _db_with(tmp_path, "a.db", 20262027), now=datetime(2026, 10, 10, tzinfo=UTC))
    d = remote.drill(backend, crypto.new_key(), tmp_path, DB_DIR, datetime(2026, 10, 11, tzinfo=UTC))
    assert not d.ok and d.detail == {"error": "DecryptionError"}
    (tmp_path / "remote" / backend.list_assets()[0]).write_bytes(b"garbage")
    assert not remote.drill(backend, key, tmp_path, DB_DIR, datetime(2026, 10, 11, tzinfo=UTC)).ok
    empty = remote.LocalBackend(tmp_path / "empty")
    assert remote.drill(empty, key, tmp_path, DB_DIR, datetime(2026, 10, 11, tzinfo=UTC)).ok
