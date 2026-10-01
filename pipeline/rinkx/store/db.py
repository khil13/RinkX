"""SQLite access and migrations.

`db/schema.sql` is migration 1. Later changes go in `db/migrations/NNNN_name.sql`
(NNNN >= 0002) and are applied in order. `PRAGMA user_version` records the last one applied.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATION_RE = re.compile(r"^(\d{4})_[\w-]+\.sql$")


class MigrationError(RuntimeError):
    pass


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _migrations(db_dir: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = [(1, db_dir / "schema.sql")]
    mig_dir = db_dir / "migrations"
    if mig_dir.is_dir():
        for f in sorted(mig_dir.iterdir()):
            m = MIGRATION_RE.match(f.name)
            if m:
                num = int(m.group(1))
                if num < 2:
                    raise MigrationError(f"{f.name}: numbering starts at 0002 (0001 is schema.sql)")
                found.append((num, f))
    nums = [n for n, _ in found]
    if nums != list(range(1, len(nums) + 1)):
        raise MigrationError(f"migration numbers must be contiguous, got {nums}")
    return found


def migrate(conn: sqlite3.Connection, db_dir: Path) -> int:
    current = schema_version(conn)
    if current == 0:
        tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        if tables:
            raise MigrationError("database has tables but user_version = 0; refusing to guess its state")
    for num, path in _migrations(db_dir):
        if num <= current:
            continue
        conn.executescript(f"BEGIN;\n{path.read_text()}\nPRAGMA user_version = {num};\nCOMMIT;")
        current = num
    conn.execute("PRAGMA foreign_keys = ON")
    return current


def snapshot(conn: sqlite3.Connection, dest: Path) -> None:
    """Consistent copy of a live database (safe with WAL), for upload."""
    dest.unlink(missing_ok=True)
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
        out.execute("PRAGMA journal_mode = DELETE")
    finally:
        out.close()
