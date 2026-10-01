"""One pipeline run: pull store -> migrate -> stages -> publish -> push store."""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rinkx.config import Settings
from rinkx.publish.build import build_bundle, missing_setup
from rinkx.publish.schemas import Manifest
from rinkx.store import remote
from rinkx.store.db import connect, migrate, snapshot
from rinkx.timeutil import utcnow

log = logging.getLogger("rinkx")

# Stages are added phase by phase (ingest, features, models, price, alerts, grade).
STAGES: tuple[str, ...] = ("publish",)


@dataclass
class RunResult:
    manifest: Manifest
    store_pulled: str | None
    store_pushed: str | None


def run(settings: Settings, out_dir: Path, *, push: bool = True, now: datetime | None = None) -> RunResult:
    now = now or utcnow()
    settings.workdir.mkdir(parents=True, exist_ok=True)

    if settings.store_key is None:
        # Bootstrap mode: no keys yet. Publish a manifest-only site so the owner can open the
        # Setup page and generate them. Nothing is read from or written to the store.
        log.warning("not configured (missing: %s); publishing setup-only site", ", ".join(missing_setup(settings)))
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "empty.db")
            migrate(conn, settings.db_dir)
            manifest = build_bundle(conn, out_dir, settings, now=now, store_asset=None)
            conn.close()
        return RunResult(manifest, None, None)

    store_key = settings.require_store_key()
    backend = remote.backend_for(settings)
    db_path = settings.workdir / "rinkx.db"
    db_path.unlink(missing_ok=True)

    pulled = remote.pull(backend, store_key, db_path)
    log.info("store: %s", pulled or "none yet, starting a new store")
    conn = connect(db_path)
    try:
        version = migrate(conn, settings.db_dir)
        log.info("schema version %d", version)
        _run_stages(conn, settings, now)
        manifest = build_bundle(conn, out_dir, settings, now=now, store_asset=pulled)
        pushed = None
        if push:
            snap = settings.workdir / "rinkx.snapshot.db"
            snapshot(conn, snap)
            pushed = remote.push(backend, store_key, snap)  # named by wall-clock time
            snap.unlink(missing_ok=True)
            log.info("store pushed: %s", pushed)
    finally:
        conn.close()
    return RunResult(manifest, pulled, pushed)


def _run_stages(conn: sqlite3.Connection, settings: Settings, now: datetime) -> None:
    """Placeholder until Phase 1 adds ingestion. Publishing is handled by `run` itself."""
