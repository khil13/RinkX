"""Every foreign key that points at a table the pipeline deletes from has an index on its column.

With foreign keys on, deleting a parent row makes SQLite look for child rows that still point at
it. Without an index on the child column each delete scans the whole child table, so a bulk
delete grows with the square of the table sizes. That is how the projection prune went from
seconds to more than an hour in October 2026 (db/migrations/0016_projection_fk_indexes.sql)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from rinkx.config import REPO_ROOT
from rinkx.store.db import migrate

DELETES = re.compile(r"DELETE FROM ([a-z_]+)")


def _deleted_tables() -> set[str]:
    out: set[str] = set()
    for f in (REPO_ROOT / "pipeline/rinkx").rglob("*.py"):
        out |= set(DELETES.findall(f.read_text()))
    out |= {"player_projections", "game_projections"}  # project.prune deletes FROM {table}
    return out


def _indexed_first_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cols = set()
    for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
        first = conn.execute(f"PRAGMA index_info({idx[1]})").fetchone()
        if first is not None and not idx[4]:  # idx[4] = partial
            cols.add(first[2])
    pk = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[5] == 1]
    return cols | set(pk[:1])


def test_foreign_keys_into_deleted_tables_are_indexed(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "s.db")
    migrate(conn, REPO_ROOT / "db")
    deleted = _deleted_tables()
    missing = []
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for child in tables:
        indexed = _indexed_first_columns(conn, child)
        for fk in conn.execute(f"PRAGMA foreign_key_list({child})").fetchall():
            parent, col = fk[2], fk[3]
            if parent in deleted and col not in indexed:
                missing.append(f"{child}.{col} -> {parent}")
    assert not missing, f"unindexed foreign keys into tables the pipeline deletes from: {missing}"
