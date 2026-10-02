"""One pipeline run: pull store -> migrate -> stages -> publish -> push store."""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rinkx.config import Settings
from rinkx.grading.grade import run_grading
from rinkx.ingestion.http import Fetcher, HttpFetcher, ReplayFetcher
from rinkx.ingestion.nhl.jobs import NhlOptions, run_nhl
from rinkx.ingestion.odds.client import OddsClient, UrllibTransport
from rinkx.ingestion.odds.jobs import run_odds
from rinkx.models.project import run_models
from rinkx.pricing.price import run_pricing
from rinkx.publish.build import build_bundle, missing_setup
from rinkx.publish.schemas import Manifest
from rinkx.quick_entry import GhIssues, IssueTracker, finish, run_quick_entry
from rinkx.store import remote
from rinkx.store.db import connect, migrate, snapshot
from rinkx.timeutil import iso, slate_date, utcnow

log = logging.getLogger("rinkx")

# Stages are added phase by phase: ingest (Phase 1), Quick Entry + models (Phase 3), then price,
# alerts, grade.


@dataclass
class RunResult:
    manifest: Manifest
    store_pulled: str | None
    store_pushed: str | None


def run(
    settings: Settings,
    out_dir: Path,
    *,
    push: bool = True,
    now: datetime | None = None,
    tracker: IssueTracker | None = None,
    odds_client: OddsClient | None = None,
) -> RunResult:
    now = now or utcnow()
    settings.workdir.mkdir(parents=True, exist_ok=True)

    if settings.store_key is None:
        # Bootstrap mode: no keys yet. Publish a manifest-only site so the owner can open the
        # Setup page and generate them. Nothing is read from or written to the store.
        log.warning("not configured (missing: %s); publishing setup-only site", ", ".join(missing_setup(settings)))
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "empty.db")
            migrate(conn, settings.db_dir)
            manifest = build_bundle(conn, out_dir, settings, now=now, store_asset=None, today=settings.today)
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
        after_save = _run_stages(conn, settings, now, tracker, odds_client)
        manifest = build_bundle(conn, out_dir, settings, now=now, store_asset=pulled, today=settings.today)
        pushed = None
        if push:
            snap = settings.workdir / "rinkx.snapshot.db"
            snapshot(conn, snap)
            pushed = remote.push(backend, store_key, snap)  # named by wall-clock time
            snap.unlink(missing_ok=True)
            log.info("store pushed: %s", pushed)
        # Only now that the store is saved: comment on and close Quick Entry issues.
        for action in after_save:
            try:
                action()
            except Exception as exc:  # retried next run (the entry itself is already saved)
                log.warning("quick entry follow-up failed: %s", exc)
    finally:
        conn.close()
    return RunResult(manifest, pulled, pushed)


def _issue_tracker(settings: Settings) -> IssueTracker | None:
    if settings.store_backend == "github" and settings.github_repository:
        return GhIssues(settings.github_repository)
    return None


def _run_stages(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    tracker: IssueTracker | None,
    odds_client: OddsClient | None = None,
) -> list[Callable[[], None]]:
    """Run every stage; return follow-up actions to perform once the store is saved."""
    if "nhl" in settings.sources:
        fetcher: Fetcher = ReplayFetcher(settings.fixtures_dir) if settings.fixtures_dir is not None else HttpFetcher()
        run_nhl(conn, fetcher, now, NhlOptions(today=settings.today, boxscore_limit=settings.boxscore_limit))

    # Sportsbook lines: only with an ODDS_API_KEY (or an injected client in tests).
    if odds_client is None and settings.odds_api_key and "nhl" in settings.sources:
        odds_client = OddsClient(settings.odds_api_key, UrllibTransport())
    if odds_client is not None:
        run_odds(conn, odds_client, now)

    tracker = tracker or _issue_tracker(settings)
    started = iso(now)
    qe = None
    if tracker is not None and settings.github_repository:
        owner = settings.github_repository.split("/")[0]
        qe = run_quick_entry(conn, tracker, owner, now)

    today = settings.today or slate_date(now)
    run_models(conn, now, today, qe.reasons if qe else None)
    run_pricing(conn, now)  # model vs market for every open line with a current projection
    run_grading(conn, now, today)  # settle predictions for finished games
    conn.commit()
    return finish(conn, tracker, qe, started) if tracker is not None and qe is not None else []
