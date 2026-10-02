"""Data-source registration and ingestion-run bookkeeping.

Every job runs inside `ingestion_run(...)`, which records start/finish, row counts, HTTP
calls and errors in `ingestion_runs`. Feed freshness on the site is computed from these
rows, so a failing source is visible instead of silently stale.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from rinkx.timeutil import iso, utcnow

log = logging.getLogger("rinkx.ingestion")


@dataclass(frozen=True)
class SourceSpec:
    code: str
    name: str
    category: str
    tier: str
    base_url: str
    license_notes: str
    default_quality: float = 0.95


def register_source(conn: sqlite3.Connection, spec: SourceSpec) -> int:
    conn.execute(
        "INSERT INTO data_sources (code, name, category, tier, base_url, license_notes, default_quality) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name = excluded.name, "
        "category = excluded.category, tier = excluded.tier, base_url = excluded.base_url, "
        "license_notes = excluded.license_notes, default_quality = excluded.default_quality",
        (spec.code, spec.name, spec.category, spec.tier, spec.base_url, spec.license_notes, spec.default_quality),
    )
    return int(conn.execute("SELECT id FROM data_sources WHERE code = ?", (spec.code,)).fetchone()[0])


def last_success(conn: sqlite3.Connection, job_name: str) -> str | None:
    row = conn.execute(
        "SELECT max(finished_at) FROM ingestion_runs WHERE job_name = ? AND status IN ('succeeded', 'partial')",
        (job_name,),
    ).fetchone()
    return row[0] if row else None


def source_enabled(conn: sqlite3.Connection, code: str) -> bool:
    row = conn.execute("SELECT is_enabled FROM data_sources WHERE code = ?", (code,)).fetchone()
    return row is None or bool(row[0])


@dataclass
class RunStats:
    rows_read: int = 0
    rows_upserted: int = 0
    http_calls: int = 0
    quota_remaining: int | None = None  # metered APIs (odds)
    errors: list[str] = field(default_factory=list)  # per-item failures; the run is then 'partial'
    meta: dict[str, object] = field(default_factory=dict)


@contextmanager
def ingestion_run(conn: sqlite3.Connection, source_id: int, job_name: str) -> Iterator[RunStats]:
    """Record a job. On error the run is marked failed and the exception is *not* re-raised,
    so one failing feed doesn't stop the others; the failure is visible on the site."""
    stats = RunStats()
    run_id = conn.execute(
        "INSERT INTO ingestion_runs (source_id, job_name, workflow_run_id, status, started_at) "
        "VALUES (?, ?, ?, 'running', ?)",
        (source_id, job_name, os.environ.get("GITHUB_RUN_ID"), iso(utcnow())),
    ).lastrowid
    conn.commit()
    status, error = "succeeded", None
    try:
        yield stats
        conn.commit()
        if stats.errors:
            status = "partial"
            stats.meta["errors"] = stats.errors[:20]
            stats.meta["error_count"] = len(stats.errors)
    except Exception as exc:
        conn.rollback()
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        log.error("%s failed: %s\n%s", job_name, error, traceback.format_exc())
    conn.execute(
        "UPDATE ingestion_runs SET status = ?, finished_at = ?, rows_read = ?, rows_upserted = ?, "
        "http_calls = ?, quota_remaining = ?, error = ?, meta = ? WHERE id = ?",
        (
            status,
            iso(utcnow()),
            stats.rows_read,
            stats.rows_upserted,
            stats.http_calls,
            stats.quota_remaining,
            error,
            json.dumps(stats.meta),
            run_id,
        ),
    )
    conn.commit()
