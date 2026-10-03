"""Store -> published bundle (manifest + keyfile + encrypted JSON files)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from rinkx import __version__, crypto
from rinkx.alerts import evaluate as alerts
from rinkx.config import ConfigError, Settings
from rinkx.correlation import estimate as correlations
from rinkx.correlation import sim
from rinkx.grading import performance
from rinkx.ingestion.injuries import espn as injuries
from rinkx.ingestion.nhl.jobs import current_season
from rinkx.models import deployment
from rinkx.publish import backtest, best, guards, lines, news, projections, views
from rinkx.publish.schemas import BuildInfo, Envelope, FeedStatus, FileEntry, Manifest, Meta
from rinkx.store import remote
from rinkx.store.db import schema_version
from rinkx.timeutil import iso, parse_iso, slate_date

# Feeds the app depends on, the data_sources.category that serves each one, and how old the
# last successful fetch may be before the UI flags it as stale.
FEEDS: list[tuple[str, str, str, timedelta]] = [
    ("schedule", "NHL schedule & scores", "schedule", timedelta(hours=24)),
    ("stats", "Player & team stats", "stats", timedelta(hours=36)),
    ("lineups", "Lineups & PP units", "lineups", timedelta(hours=24)),
    ("goalies", "Starting goalies", "goalies", timedelta(hours=6)),
    ("injuries", "Injuries & availability", "injuries", timedelta(hours=6)),
    ("odds", "Sportsbook lines", "odds", timedelta(hours=2)),
    ("news", "News", "news", timedelta(hours=6)),
]

MANIFEST = "manifest.json"
KEYFILE = "keyfile.json"


def missing_setup(settings: Settings) -> list[str]:
    missing = []
    if not settings.keyfile.is_file():
        missing.append("keyfile")
    if settings.data_key is None:
        missing.append("DATA_KEY")
    if settings.store_key is None:
        missing.append("STORE_KEY")
    return missing


def feed_statuses(conn: sqlite3.Connection, now: datetime) -> list[FeedStatus]:
    out = []
    for code, name, category, max_age in FEEDS:
        last_ok = conn.execute(
            "SELECT max(r.finished_at) FROM ingestion_runs r JOIN data_sources s ON s.id = r.source_id "
            "WHERE s.category = ? AND s.is_enabled = 1 AND r.status IN ('succeeded', 'partial')",
            (category,),
        ).fetchone()[0]
        last_any = conn.execute(
            "SELECT r.status FROM ingestion_runs r JOIN data_sources s ON s.id = r.source_id "
            "WHERE s.category = ? AND s.is_enabled = 1 AND r.finished_at IS NOT NULL "
            "ORDER BY r.finished_at DESC LIMIT 1",
            (category,),
        ).fetchone()
        if last_ok is None and last_any is not None and last_any[0] == "failed":
            out.append(
                FeedStatus(
                    code=code,
                    name=name,
                    state="failed",
                    last_success_at=None,
                    reason="Fetch failed; no successful fetch yet",
                )
            )
        elif last_ok is None:
            out.append(
                FeedStatus(
                    code=code, name=name, state="unavailable", last_success_at=None, reason="No successful fetch yet"
                )
            )
        elif last_any is not None and last_any[0] == "failed":
            out.append(
                FeedStatus(
                    code=code, name=name, state="failed", last_success_at=last_ok, reason="Most recent fetch failed"
                )
            )
        elif now - parse_iso(last_ok) > max_age:
            out.append(
                FeedStatus(
                    code=code,
                    name=name,
                    state="stale",
                    last_success_at=last_ok,
                    reason=f"Older than {int(max_age.total_seconds() // 3600)} h",
                )
            )
        else:
            out.append(FeedStatus(code=code, name=name, state="ok", last_success_at=last_ok))
    return out


DRILL_SQL = """
SELECT r.status, r.finished_at, r.meta, r.error FROM ingestion_runs r JOIN data_sources s ON s.id = r.source_id
WHERE s.code = 'store' AND r.job_name = 'restore_drill' ORDER BY r.id DESC LIMIT 1
"""


def _drill(conn: sqlite3.Connection) -> dict[str, Any] | None:
    r = conn.execute(DRILL_SQL).fetchone()
    if r is None:
        return None
    return {"status": r["status"], "at": r["finished_at"], "detail": json.loads(r["meta"] or "{}"), "error": r["error"]}


def _health(
    conn: sqlite3.Connection, store_asset: str | None, build: BuildInfo, versions: list[dict[str, Any]]
) -> dict[str, Any]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    sources = [
        dict(r) for r in conn.execute("SELECT code, name, category, tier, is_enabled FROM data_sources ORDER BY code")
    ]
    runs = [
        dict(r)
        for r in conn.execute(
            "SELECT r.job_name, s.code AS source, r.status, r.started_at, r.finished_at, r.rows_upserted, "
            "r.quota_remaining, r.error FROM ingestion_runs r JOIN data_sources s ON s.id = r.source_id "
            "ORDER BY r.started_at DESC LIMIT 50"
        )
    ]
    return {
        "build": build.model_dump(),
        "store": {
            "asset": store_asset,
            "schema_version": schema_version(conn),
            "versions": versions,  # as found at the start of this run (this run's upload comes after)
            "keep": {"recent": remote.KEEP_VERSIONS, "weekly": remote.WEEKLY_SNAPSHOTS},
            "drill": _drill(conn),
        },
        "table_rows": {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables},
        "synthetic_rows": guards.synthetic_row_counts(conn),
        "odds": lines.odds_admin(conn),
        "injuries": injuries.admin(conn),
        "data_sources": sources,
        "recent_runs": runs,
    }


SLATE_DAYS_BACK = 3
SLATE_DAYS_AHEAD = 3


def _league_files(
    conn: sqlite3.Connection, today: date, now: datetime, delivery: bool = False
) -> dict[str, tuple[Any, str | None]]:
    """Slates for a week around today, their games, teams, and players."""
    out: dict[str, tuple[Any, str | None]] = {}
    season = current_season(conn, today)
    # An empty slate must mean "no games that day", never "we have no schedule data", so a
    # slate is only published for dates a successful schedule fetch actually covered.
    for offset in range(-SLATE_DAYS_BACK, SLATE_DAYS_AHEAD + 1):
        d = (today + timedelta(days=offset)).isoformat()
        if not conn.execute("SELECT 1 FROM schedule_coverage WHERE game_date = ?", (d,)).fetchone():
            continue
        oldest = conn.execute("SELECT min(fetched_at) FROM games WHERE game_date = ?", (d,)).fetchone()[0]
        out[f"slate/{d}.json"] = (views.slate(conn, d), oldest)
        for (gid,) in conn.execute("SELECT nhl_game_id FROM games WHERE game_date = ?", (d,)).fetchall():
            out[f"games/{gid}.json"] = (views.game_detail(conn, gid), oldest)
    assist_model = sim.assist_model(conn)
    for g in conn.execute(
        "SELECT * FROM games WHERE status IN ('scheduled','pregame') AND game_date >= ? AND id IN "
        "(SELECT game_id FROM player_projections WHERE is_current = 1)",
        (today.isoformat(),),
    ).fetchall():
        inputs = sim.game_inputs(conn, g, assist_model)
        if inputs is not None:
            out[f"sim/{g['nhl_game_id']}.json"] = (inputs, None)
    out["backtest.json"] = (backtest.published(conn, now), None)
    out["deployment.json"] = (deployment.published(conn, now, today.isoformat()), None)
    out["models.json"] = (projections.models_report(conn), None)
    out["props/best.json"] = (best.best_props(conn, now), None)
    out["performance.json"] = (performance.performance(conn, now), None)
    out["news.json"] = (news.feed(conn, now), None)
    out["alerts.json"] = (alerts.history(conn, now, delivery), None)
    out["correlations.json"] = (correlations.published(conn, now), None)
    out["lines/movement.json"] = (lines.movement_board(conn, now), None)
    if conn.execute("SELECT 1 FROM teams LIMIT 1").fetchone():
        out["teams.json"] = (views.teams(conn, season, today.isoformat()), None)
    if conn.execute("SELECT 1 FROM players LIMIT 1").fetchone():
        out["players/index.json"] = (views.players_index(conn), None)
        for (pid,) in conn.execute("SELECT nhl_player_id FROM players WHERE is_active = 1").fetchall():
            out[f"players/{pid}.json"] = (views.player(conn, pid, season, now), None)
    return out


def _write(root: Path, rel: str, payload: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def _replace_output(staging: Path, out_dir: Path) -> None:
    if out_dir.exists():
        if any(out_dir.iterdir()) and not (out_dir / MANIFEST).is_file():
            raise ConfigError(f"{out_dir} is not empty and is not a previous RinkX bundle; refusing to replace it")
        shutil.rmtree(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(out_dir))


def build_bundle(
    conn: sqlite3.Connection,
    out_dir: Path,
    settings: Settings,
    *,
    now: datetime,
    store_asset: str | None,
    today: date | None = None,
    store_versions: list[dict[str, Any]] | None = None,
) -> Manifest:
    today = today or slate_date(now)
    if settings.env == "prod":
        guards.assert_no_synthetic(conn)

    missing = missing_setup(settings)
    configured = not missing
    build = BuildInfo(
        app_version=__version__,
        git_sha=os.environ.get("GITHUB_SHA"),
        run_url=(
            f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            if all(k in os.environ for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
            else None
        ),
    )

    staging = Path(tempfile.mkdtemp(prefix="rinkx-bundle-"))
    files: dict[str, FileEntry] = {}

    if configured:
        data_key = settings.require_data_key()
        keyfile = json.loads(settings.keyfile.read_text())
        if not crypto.keyfile_matches(keyfile, data_key):
            raise ConfigError("DATA_KEY does not match keyfile.json; the passphrase could not unlock this bundle")
        _write(staging, KEYFILE, keyfile)

        synthetic = bool(guards.synthetic_row_counts(conn))
        sources = [
            r[0]
            for r in conn.execute(
                "SELECT code FROM data_sources WHERE is_enabled = 1 AND category NOT IN ('models', 'alerts', 'store') "
                "ORDER BY code"
            )
        ]
        model_versions = {
            r[0]: r[1]
            for r in conn.execute("SELECT model_family, version FROM model_versions WHERE status = 'champion'")
        }
        encrypted: dict[str, tuple[Any, str | None]] = {
            "admin/health.json": (_health(conn, store_asset, build, store_versions or []), None),
        }
        encrypted.update(_league_files(conn, today, now, delivery=settings.ntfy_topic is not None))
        for rel, (data, oldest) in encrypted.items():
            env = Envelope(
                data=data,
                meta=Meta(
                    generated_at=iso(now),
                    data_status="synthetic" if synthetic else "live",
                    oldest_input_at=oldest,
                    sources=sources,
                    model_versions=model_versions,
                ),
            )
            _write(staging, rel + ".enc", crypto.encrypt_json(env.model_dump(mode="json"), rel, data_key))

    for path in sorted(staging.rglob("*")):
        if path.is_file():
            raw = path.read_bytes()
            files[path.relative_to(staging).as_posix()] = FileEntry(
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)
            )

    manifest = Manifest(
        generated_at=iso(now),
        env=settings.env,
        configured=configured,
        missing_setup=missing,
        slate_date=today.isoformat(),
        build=build,
        feeds=feed_statuses(conn, now),
        files=files,
    )
    _write(staging, MANIFEST, manifest.model_dump(mode="json"))
    _replace_output(staging, out_dir)
    return manifest
