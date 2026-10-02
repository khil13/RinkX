"""Build the sites the e2e tests run against, using the real pipeline.

  .sites/league      dist/ + the real pipeline replaying recorded NHL responses (2026-03-10)
  .sites/empty       dist/ + a configured bundle with no data sources connected
  .sites/setup       dist/ + a manifest-only bundle (no keys yet)
  .sites/models      dist/ + a DEV bundle from the SYNTHETIC test league (tests/synth.py): tested
                     models, projections for an upcoming game, and a Quick-Entry goalie change

Run from web/ after `vite build`:  python tests/e2e/prepare.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1]
REPO = WEB.parent
sys.path.insert(0, str(REPO / "pipeline"))
sys.path.insert(0, str(REPO / "pipeline/tests"))

from rinkx import crypto  # noqa: E402
from rinkx.config import Settings  # noqa: E402
from rinkx.models.project import run_models  # noqa: E402
from rinkx.pricing.price import run_pricing  # noqa: E402
from rinkx.pipeline import run  # noqa: E402
from rinkx.publish.build import build_bundle  # noqa: E402
from rinkx.quick_entry import Issue, run_quick_entry  # noqa: E402
from rinkx.timeutil import iso  # noqa: E402

PASSPHRASE = "e2e passphrase with several words"
SITES = HERE / ".sites"


def site(name: str) -> Path:
    dest = SITES / name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(WEB / "dist", dest)
    return dest


def main() -> None:
    if not (WEB / "dist/index.html").is_file():
        raise SystemExit("run `vite build` first")
    SITES.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="rinkx-e2e-"))
    base = {
        "RINKX_ENV": "prod",
        "RINKX_STORE_BACKEND": "local",
        "RINKX_STORE_DIR": str(tmp / "remote"),
        "RINKX_WORKDIR": str(tmp / "work"),
        "RINKX_DB_DIR": str(REPO / "db"),
        "RINKX_SOURCES": "none",
    }

    # Configured sites share production keyfile parameters (600k iterations) and prod env.
    data_key, store_key = crypto.new_key(), crypto.new_key()
    keyfile = tmp / "keyfile.json"
    keyfile.write_text(json.dumps(crypto.make_keyfile(PASSPHRASE, data_key)))
    keys = {
        "RINKX_KEYFILE": str(keyfile),
        "RINKX_DATA_KEY": crypto.b64e(data_key),
        "RINKX_STORE_KEY": crypto.b64e(store_key),
    }

    # League site: the real pipeline ingesting the recorded NHL responses for 2026-03-10.
    run(
        Settings.from_env(
            base
            | keys
            | {
                "RINKX_SOURCES": "nhl",
                "RINKX_FIXTURES": str(REPO / "pipeline/tests/fixtures/nhl"),
                "RINKX_TODAY": "2026-03-10",
                "RINKX_BOXSCORE_LIMIT": "100",
                "RINKX_STORE_DIR": str(tmp / "remote-league"),
            }
        ),
        site("league") / "data",
    )

    # Empty site: configured, but no data source has ever run.
    run(Settings.from_env(base | keys | {"RINKX_STORE_DIR": str(tmp / "remote-empty")}), site("empty") / "data")

    # Unconfigured site: what Pages serves before the owner completes setup.
    setup = site("setup")
    run(Settings.from_env(base | {"RINKX_KEYFILE": str(tmp / "absent.json")}), setup / "data")

    models_site(base | keys, tmp)

    (SITES / "secrets.json").write_text(json.dumps({"passphrase": PASSPHRASE}))
    shutil.rmtree(tmp)
    print(f"e2e sites ready in {SITES}")


class _Tracker:
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues

    def open_issues(self) -> list[Issue]:
        return self.issues

    def comment(self, number: int, body: str) -> None: ...

    def close(self, number: int, completed: bool) -> None: ...


class _OddsTransport:
    """Serves SYNTHETIC odds payloads (DEV site only) in The Odds API v4 shape."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes, self.calls, self.remaining = routes, 0, 500

    def get(self, url: str) -> tuple[object, dict[str, str]]:
        import urllib.parse

        self.calls += 1
        path = urllib.parse.urlparse(url).path.removeprefix("/v4")
        cost = 0 if path.endswith("/events") else 3
        self.remaining -= cost
        return self.routes[path], {"x-requests-last": str(cost), "x-requests-remaining": str(self.remaining)}


def _synthetic_lines(conn, game: int, first: datetime, second: datetime) -> None:
    from rinkx.ingestion.odds.client import OddsClient
    from rinkx.ingestion.odds.jobs import OddsConfig, run_odds

    g = conn.execute(
        "SELECT g.start_time_utc, h.location || ' ' || h.name, a.location || ' ' || a.name, g.home_team_id "
        "FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id WHERE g.id = ?",
        (game,),
    ).fetchone()
    ev = {"id": "syn-ev", "commence_time": g[0], "home_team": g[1], "away_team": g[2]}
    skaters = [r[0] for r in conn.execute(
        "SELECT full_name FROM players WHERE current_team_id = ? AND position <> 'G' ORDER BY id LIMIT 3", (g[3],)
    )]

    def payload(move: int) -> dict:
        def book(key: str, shift: int) -> dict:
            outs = []
            for i, name in enumerate(skaters):
                outs += [{"name": "Over", "description": name, "price": -120 - 10 * i + shift - move, "point": 2.5},
                         {"name": "Under", "description": name, "price": -110 + 5 * i - shift, "point": 2.5}]
            return {"key": key, "title": key, "markets": [{"key": "player_shots_on_goal", "outcomes": outs}]}

        return ev | {"bookmakers": [book("fanduel", 0), book("betmgm", 5)]}

    game_odds = [ev | {"bookmakers": [{"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
        {"name": g[1], "price": -135}, {"name": g[2], "price": 115}]}]}]}]
    cfg = OddsConfig.load()
    for at, move in ((first, 0), (second + timedelta(hours=6), 15)):
        routes = {"/sports/icehockey_nhl/events": [ev], "/sports/icehockey_nhl/odds": game_odds,
                  "/sports/icehockey_nhl/events/syn-ev/odds": payload(move)}
        run_odds(conn, OddsClient("synthetic-dev-key", _OddsTransport(routes)), at, cfg)
        run_pricing(conn, at)  # Phase 5: model vs market for each line


def models_site(env: dict[str, str], tmp: Path) -> None:
    """Synthetic league (labelled: DEV build, SYNTHETIC data): models tested, projections published,
    then the away team's backup goalie confirmed through Quick Entry so before/after shows."""
    import synth  # pipeline/tests/synth.py

    days = 200
    today = date(2025, 10, 7) + timedelta(days=days)
    now = datetime(today.year, today.month, today.day, 15, tzinfo=UTC)
    conn, truth = synth.build(str(tmp / "synthetic.db"), teams=20, days=days, seed=22)
    game = synth.add_upcoming(conn, today)
    run_models(conn, now, today)
    away = conn.execute("SELECT away_team_id FROM games WHERE id = ?", (game,)).fetchone()[0]
    nhl_id, abbrev = conn.execute(
        "SELECT p.nhl_player_id, t.abbrev FROM players p JOIN teams t ON t.id = p.current_team_id WHERE p.id = ?",
        (truth.goalies[away][1],),
    ).fetchone()
    body = (
        f"### Game\n\n{synth.UPCOMING_NHL_ID}\n\n### Team\n\n{abbrev}\n\n### Goalie\n\n{nhl_id}\n\n"
        "### Status\n\nConfirmed\n\n### Source URL\n\nhttps://example.org/synthetic-source\n"
    )
    later = now + timedelta(hours=1)
    qe = run_quick_entry(conn, _Tracker([Issue(1, "Quick Entry: goalie", body, "owner", iso(now))]), "owner", later)
    run_models(conn, later, today, qe.reasons)
    _synthetic_lines(conn, game, now, later)
    settings = Settings.from_env(env | {"RINKX_ENV": "dev"})
    build_bundle(conn, site("models") / "data", settings, now=later, store_asset=None, today=today)
    conn.close()


if __name__ == "__main__":
    main()
