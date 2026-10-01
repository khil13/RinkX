"""Checks that must pass before anything is published."""

from __future__ import annotations

import sqlite3


class PublishBlocked(RuntimeError):
    pass


def tables_with_provenance(conn: sqlite3.Connection) -> list[str]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return [t for t in tables if any(c[1] == "provenance" for c in conn.execute(f"PRAGMA table_info({t})"))]


def synthetic_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for t in tables_with_provenance(conn):
        n = conn.execute(f"SELECT count(*) FROM {t} WHERE provenance = 'synthetic'").fetchone()[0]
        if n:
            counts[t] = int(n)
    return counts


def assert_no_synthetic(conn: sqlite3.Connection) -> None:
    counts = synthetic_row_counts(conn)
    if counts:
        detail = ", ".join(f"{t}={n}" for t, n in sorted(counts.items()))
        raise PublishBlocked(f"synthetic rows present in prod store ({detail}); refusing to publish")
