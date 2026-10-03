"""Alerts (Phase 8): config/alerts.yml -> `alerts`, evaluated every run, delivered through ntfy.

Each alert fires at most once per game (a unique index on alert_events). If several props in a
game match when it fires, they are all listed in that one notification. Events are recorded
whether or not delivery is configured, and the app shows them. Delivery happens before the store
is saved, so a sent notification is marked sent. Notification text and the ntfy topic never go
to the logs: the repository and its Actions logs are public.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rinkx.config import REPO_ROOT
from rinkx.ingestion.runs import SourceSpec, ingestion_run, register_source
from rinkx.models import deployment
from rinkx.pricing.odds import implied
from rinkx.publish.best import best_props
from rinkx.timeutil import iso

log = logging.getLogger("rinkx.alerts")

ALERTS_FILE = REPO_ROOT / "config/alerts.yml"
SOURCE = SourceSpec("alerts", "Alerts (config/alerts.yml)", "alerts", "free", "", "Evaluated each run.", 1.0)
TYPES = {
    "edge": "edge_threshold",
    "line_move": "line_move",
    "goalie": "goalie_confirmed",
    "news": "injury_news",
    "line": "line_threshold",
    "deployment": "deployment",
    "projection": "projection_change",
    "scratch": "scratch",
}
OPTIONS = {
    "edge": {"min_edge", "min_confidence", "min_ev", "market", "player", "team"},
    "line_move": {"points", "market", "player", "team"},
    "goalie": {"team"},
    "news": {"categories", "player", "team"},
    "line": {"market", "player", "max_line", "min_price"},
    "deployment": {"changes", "player", "team"},
    "projection": {"min_change_pct", "direction", "market", "player", "team"},
    "scratch": {"player", "team"},
}
DEPLOYMENT_CHANGES = set(deployment.CHANGE_TEXT)
NEWS_CATEGORIES = {"injury", "lineup", "goalie", "scratch", "suspension", "coach", "rest", "transaction", "general"}
WINDOW = timedelta(hours=48)  # games starting within this window are watched
NEWS_RECENT = timedelta(hours=36)
RETRY_WINDOW = timedelta(hours=6)  # undelivered events older than this are not sent any more
MAX_LINES = 5

Sender = Callable[[dict[str, Any]], None]  # raises on failure
Games = dict[int, sqlite3.Row]
Hits = Iterator[tuple[sqlite3.Row, str]]  # (game, one line of the notification)


class AlertConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AlertSpec:
    key: str
    type: str
    enabled: bool
    options: dict[str, Any]


def load_specs(path: Path = ALERTS_FILE) -> tuple[list[AlertSpec], list[str]]:
    """Valid alerts, plus one message per invalid entry (a bad entry never stops the run)."""
    if not path.is_file():
        return [], []
    doc = yaml.safe_load(path.read_text()) or {}
    specs: list[AlertSpec] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(doc.get("alerts") or []):
        try:
            if not isinstance(raw, dict):
                raise AlertConfigError("each alert must be a mapping")
            key, kind = str(raw.get("key") or "").strip(), str(raw.get("type") or "").strip()
            if not key:
                raise AlertConfigError("missing key")
            if key in seen:
                raise AlertConfigError(f"duplicate key '{key}'")
            if kind not in TYPES:
                raise AlertConfigError(f"unknown type '{kind}' (use one of: {', '.join(TYPES)})")
            opts = {k: v for k, v in raw.items() if k not in ("key", "type", "enabled")}
            unknown = set(opts) - OPTIONS[kind]
            if unknown:
                raise AlertConfigError(f"unknown option(s) for {kind}: {', '.join(sorted(unknown))}")
            if kind == "line_move" and not isinstance(opts.get("points"), int | float):
                raise AlertConfigError("line_move needs `points`")
            if kind == "line" and (
                not opts.get("market") or not opts.get("player") or ("max_line" not in opts and "min_price" not in opts)
            ):
                raise AlertConfigError("line needs market, player, and max_line or min_price")
            if kind == "deployment":
                ch = opts.get("changes") or []
                if not isinstance(ch, list) or set(ch) - DEPLOYMENT_CHANGES:
                    raise AlertConfigError(f"changes must be a list of: {', '.join(sorted(DEPLOYMENT_CHANGES))}")
            if kind == "projection" and opts.get("direction", "both") not in ("up", "down", "both"):
                raise AlertConfigError("direction must be up, down or both")
            if kind == "news":
                cats = opts.get("categories") or []
                if not isinstance(cats, list) or set(cats) - NEWS_CATEGORIES:
                    raise AlertConfigError(f"categories must be a list of: {', '.join(sorted(NEWS_CATEGORIES))}")
            seen.add(key)
            specs.append(AlertSpec(key, kind, bool(raw.get("enabled", True)), opts))
        except AlertConfigError as exc:
            errors.append(f"alerts.yml entry {i + 1}: {exc}")
    return specs, errors


def sync(conn: sqlite3.Connection, specs: list[AlertSpec]) -> list[str]:
    """Upsert alerts by key; ones removed from the file are deactivated. Returns resolution errors."""
    errors: list[str] = []
    keep: set[str] = set()
    for s in specs:
        player_id = team_id = market_id = None
        if "player" in s.options:
            row = conn.execute("SELECT id FROM players WHERE nhl_player_id = ?", (int(s.options["player"]),)).fetchone()
            if row is None:
                errors.append(f"alert '{s.key}': no player with NHL id {s.options['player']}")
                continue
            player_id = row[0]
        if "team" in s.options:
            row = conn.execute("SELECT id FROM teams WHERE abbrev = ?", (str(s.options["team"]).upper(),)).fetchone()
            if row is None:
                errors.append(f"alert '{s.key}': unknown team {s.options['team']}")
                continue
            team_id = row[0]
        if "market" in s.options:
            row = conn.execute("SELECT id FROM markets WHERE code = ?", (s.options["market"],)).fetchone()
            if row is None:
                errors.append(f"alert '{s.key}': unknown market {s.options['market']}")
                continue
            market_id = row[0]
        keep.add(s.key)
        conn.execute(
            "INSERT INTO alerts (key, alert_type, player_id, team_id, market_id, condition, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (key) DO UPDATE SET alert_type = excluded.alert_type, "
            "player_id = excluded.player_id, team_id = excluded.team_id, market_id = excluded.market_id, "
            "condition = excluded.condition, is_active = excluded.is_active",
            (
                s.key,
                TYPES[s.type],
                player_id,
                team_id,
                market_id,
                json.dumps({"type": s.type} | s.options),
                int(s.enabled),
            ),
        )
    for (key,) in conn.execute("SELECT key FROM alerts").fetchall():
        if key not in keep:
            conn.execute("UPDATE alerts SET is_active = 0 WHERE key = ?", (key,))
    return errors


# ---- matching ---------------------------------------------------------------------------------


@dataclass
class Match:
    game_id: int  # games.id
    nhl_game_id: int
    matchup: str
    lines: list[str] = field(default_factory=list)


def _upcoming(conn: sqlite3.Connection, now: datetime) -> dict[int, sqlite3.Row]:
    rows = conn.execute(
        "SELECT g.id, g.nhl_game_id, g.home_team_id, g.away_team_id, a.abbrev || ' @ ' || h.abbrev AS matchup "
        "FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id "
        "WHERE g.status IN ('scheduled','pregame') AND g.start_time_utc > ? AND g.start_time_utc <= ?",
        (iso(now), iso(now + WINDOW)),
    ).fetchall()
    return {r["id"]: r for r in rows}


def _american(p: int | None) -> str:
    return "—" if p is None else f"{p:+d}"


def _edge(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    by_nhl = {g["nhl_game_id"]: g for g in games.values()}
    min_edge = float(c.get("min_edge", 3)) / 100
    for r in best_props(conn, now)["rows"]:
        g = by_nhl.get(r["game"]["id"])
        if g is None or not r["lean"] or (r["edge"] or 0) < min_edge:
            continue
        if (r["confidence"] or 0) < float(c.get("min_confidence", 0)) or (r["ev"] or 0) < float(c.get("min_ev", 0)):
            continue
        if c.get("market") and r["market"] != c["market"]:
            continue
        if c.get("player") and r["subject"].get("id") != int(c["player"]):
            continue
        if c.get("team") and r["subject"].get("team") != str(c["team"]).upper():
            continue
        side = {"over": "Over", "under": "Under", "yes": "Yes", "no": "No", "home": "Home", "away": "Away"}[r["lean"]]
        line = f" {r['line']:g}" if r["line"] is not None else ""
        yield (
            g,
            f"{r['subject']['name']} {side}{line} {r['market_label']} {_american(r['price'])} at {r['book_name']}: "
            f"edge {r['edge'] * 100:+.1f} pts, confidence {r['confidence']}",
        )


def _line_rows(conn: sqlite3.Connection, games: dict[int, sqlite3.Row]) -> list[sqlite3.Row]:
    if not games:
        return []
    marks = ",".join("?" * len(games))
    return conn.execute(
        "SELECT l.*, m.code AS market, m.name AS market_name, b.name AS book_name, p.full_name, "
        "p.nhl_player_id, t.abbrev AS team, "
        "(SELECT over_price FROM line_movements x WHERE x.prop_line_id = l.id AND x.over_price IS NOT NULL "
        " ORDER BY observed_at LIMIT 1) AS first_over "
        "FROM prop_lines l JOIN markets m ON m.id = l.market_id JOIN sportsbooks b ON b.id = l.sportsbook_id "
        "LEFT JOIN players p ON p.id = l.player_id LEFT JOIN teams t ON t.id = p.current_team_id "
        f"WHERE l.status = 'open' AND l.is_main_line = 1 AND b.is_enabled = 1 AND l.game_id IN ({marks})",
        tuple(games),
    ).fetchall()


def _filters_ok(r: sqlite3.Row, c: dict[str, Any]) -> bool:
    if c.get("market") and r["market"] != c["market"]:
        return False
    if c.get("player") and r["nhl_player_id"] != int(c["player"]):
        return False
    return not (c.get("team") and r["team"] != str(c["team"]).upper())


def _line_move(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    for r in _line_rows(conn, games):
        if r["first_over"] is None or r["over_price"] is None or not _filters_ok(r, c):
            continue
        moved = (implied(r["over_price"]) - implied(r["first_over"])) * 100
        if abs(moved) >= float(c["points"]):
            who = r["full_name"] or games[r["game_id"]]["matchup"]
            line = f" {r['line']:g}" if r["line"] is not None else ""
            yield (
                games[r["game_id"]],
                f"{who} {r['market_name']}{line} at {r['book_name']}: over {_american(r['first_over'])} → "
                f"{_american(r['over_price'])} ({moved:+.1f} pts)",
            )


def _line(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    for r in _line_rows(conn, games):
        if not _filters_ok(r, c):
            continue
        ok_line = "max_line" in c and r["line"] is not None and r["line"] <= float(c["max_line"])
        ok_price = "min_price" in c and r["over_price"] is not None and r["over_price"] >= int(c["min_price"])
        if ok_line or ok_price:
            line = f" {r['line']:g}" if r["line"] is not None else ""
            yield (
                games[r["game_id"]],
                f"{r['full_name']} {r['market_name']}{line} at {r['book_name']}: over {_american(r['over_price'])}, "
                f"under {_american(r['under_price'])}",
            )


def _goalie(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    for g in games.values():
        for team in (g["away_team_id"], g["home_team_id"]):
            if a["team_id"] is not None and team != a["team_id"]:
                continue
            row = conn.execute(
                "SELECT s.status, p.full_name, t.abbrev FROM goalie_starts s JOIN players p ON p.id = s.player_id "
                "JOIN teams t ON t.id = s.team_id WHERE s.game_id = ? AND s.team_id = ? "
                "ORDER BY s.reported_at DESC, s.id DESC LIMIT 1",
                (g["id"], team),
            ).fetchone()
            if row is not None and row["status"] == "confirmed":
                yield g, f"{row['abbrev']}: {row['full_name']} confirmed to start"


def _news(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    cats = set(c.get("categories") or [])
    rows = conn.execute(
        "SELECT n.id, n.headline, n.url, n.category, e.player_id, coalesce(e.team_id, p.current_team_id) AS team_id "
        "FROM news n JOIN news_entities e ON e.news_id = n.id LEFT JOIN players p ON p.id = e.player_id "
        "WHERE n.published_at >= ? ORDER BY n.published_at",
        (iso(now - NEWS_RECENT),),
    ).fetchall()
    for r in rows:
        if cats and r["category"] not in cats:
            continue
        if a["player_id"] is not None and r["player_id"] != a["player_id"]:
            continue
        if a["team_id"] is not None and r["team_id"] != a["team_id"]:
            continue
        for g in games.values():
            if r["team_id"] in (g["home_team_id"], g["away_team_id"]):
                yield g, f"{r['category'].title()}: {r['headline']} ({r['url']})"
                break


def _games_rows(conn: sqlite3.Connection, games: Games) -> list[sqlite3.Row]:
    if not games:
        return []
    marks = ",".join("?" * len(games))
    return conn.execute(f"SELECT * FROM games WHERE id IN ({marks})", tuple(games)).fetchall()


def _deployment(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    kinds = set(c.get("changes") or [])
    for g in _games_rows(conn, games):
        for text in deployment.alert_lines(conn, g, kinds, a["player_id"], a["team_id"]):
            yield games[g["id"]], text


def _projection(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    min_pct = float(c.get("min_change_pct", 15))
    for gid, g in games.items():
        for r in deployment.projection_changes(conn, gid, min_pct, str(c.get("direction", "both"))):
            if c.get("market") and r["code"] != c["market"]:
                continue
            if a["player_id"] is not None and r["player_id"] != a["player_id"]:
                continue
            if c.get("team") and r["abbrev"] != str(c["team"]).upper():
                continue
            mark = "🔥" if r["pct"] > 0 else "⚠️"
            yield (
                g,
                f"{mark} {r['full_name']} {r['name']} projection {r['prev']:.2f} → {r['mean']:.2f} ({r['pct']:+.0f}%), "
                f"after: {str(r['trigger_reason']).replace('_', ' ')}",
            )


def _scratch(conn: sqlite3.Connection, a: sqlite3.Row, c: dict[str, Any], now: datetime, games: Games) -> Hits:
    """A player ruled out for an upcoming game as a healthy scratch (Quick Entry, with its source)."""
    for gid, g in games.items():
        for r in conn.execute(
            "SELECT av.player_id, av.source_ref, p.full_name, t.abbrev, t.id AS team_id FROM game_availability av "
            "JOIN players p ON p.id = av.player_id LEFT JOIN teams t ON t.id = p.current_team_id "
            "WHERE av.game_id = ? AND av.status = 'out' AND av.reason = 'healthy scratch'",
            (gid,),
        ):
            if a["player_id"] is not None and r["player_id"] != a["player_id"]:
                continue
            if a["team_id"] is not None and r["team_id"] != a["team_id"]:
                continue
            yield g, f"Scratch: {r['full_name']} ({r['abbrev']}) out ({r['source_ref']})"


MATCHERS = {
    "edge": _edge,
    "line_move": _line_move,
    "goalie": _goalie,
    "news": _news,
    "line": _line,
    "deployment": _deployment,
    "projection": _projection,
    "scratch": _scratch,
}


def evaluate(conn: sqlite3.Connection, now: datetime, site_url: str | None) -> int:
    """Record new alert events; returns how many fired."""
    games = _upcoming(conn, now)
    fired = 0
    for a in conn.execute("SELECT * FROM alerts WHERE is_active = 1 ORDER BY id").fetchall():
        c = json.loads(a["condition"])
        matches: dict[int, Match] = {}
        for g, text in MATCHERS[c["type"]](conn, a, c, now, games):
            m = matches.setdefault(g["id"], Match(g["id"], g["nhl_game_id"], g["matchup"]))
            if text not in m.lines:
                m.lines.append(text)
        for m in matches.values():
            body = m.lines[:MAX_LINES]
            if len(m.lines) > MAX_LINES:
                body.append(f"and {len(m.lines) - MAX_LINES} more")
            payload = {
                "title": f"RinkX · {a['key']} · {m.matchup}",
                "message": "\n".join(body),
                "click": f"{site_url}#/games/{m.nhl_game_id}" if site_url else None,
                "game": m.nhl_game_id,
                "matchup": m.matchup,
                "matches": len(m.lines),
                "type": c["type"],
            }
            cur = conn.execute(
                "INSERT OR IGNORE INTO alert_events (alert_id, game_id, triggered_at, payload) VALUES (?, ?, ?, ?)",
                (a["id"], m.game_id, iso(now), json.dumps(payload)),
            )
            if cur.rowcount:
                fired += 1
                conn.execute("UPDATE alerts SET last_triggered_at = ? WHERE id = ?", (iso(now), a["id"]))
    return fired


def deliver(conn: sqlite3.Connection, now: datetime, send: Sender | None) -> tuple[int, int]:
    """Send undelivered recent events. Returns (sent, failed). Without a sender they stay pending."""
    if send is None:
        return 0, 0
    sent = failed = 0
    rows = conn.execute(
        "SELECT id, payload FROM alert_events WHERE delivery_status IN ('pending','failed') AND triggered_at >= ? "
        "ORDER BY id",
        (iso(now - RETRY_WINDOW),),
    ).fetchall()
    for r in rows:
        try:
            send(json.loads(r["payload"]))
        except Exception as exc:  # the message and topic are never logged (public logs)
            log.warning("alert delivery failed (%s)", type(exc).__name__)
            conn.execute("UPDATE alert_events SET delivery_status = 'failed' WHERE id = ?", (r["id"],))
            failed += 1
            continue
        conn.execute(
            "UPDATE alert_events SET delivery_status = 'sent', delivered_at = ? WHERE id = ?", (iso(now), r["id"])
        )
        sent += 1
    return sent, failed


def run_alerts(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    send: Sender | None,
    site_url: str | None,
    path: Path = ALERTS_FILE,
) -> int:
    src = register_source(conn, SOURCE)
    with ingestion_run(conn, src, "alerts") as run:
        specs, errors = load_specs(path)
        errors += sync(conn, specs)
        run.errors.extend(errors)
        fired = evaluate(conn, now, site_url)
        sent, failed = deliver(conn, now, send)
        run.rows_upserted = fired
        run.meta.update(fired=fired, sent=sent, failed=failed, delivery="ntfy" if send else "not configured")
    return fired


def history(conn: sqlite3.Connection, now: datetime, delivery: bool) -> dict[str, Any]:
    """Published as alerts.json: the configured alerts and the last 14 days of events."""
    alerts = [
        {
            "key": r["key"],
            "type": json.loads(r["condition"]).get("type"),
            "active": bool(r["is_active"]),
            "condition": {k: v for k, v in json.loads(r["condition"]).items() if k != "type"},
            "last_triggered_at": r["last_triggered_at"],
        }
        for r in conn.execute("SELECT * FROM alerts ORDER BY is_active DESC, key")
    ]
    events = []
    for r in conn.execute(
        "SELECT e.*, a.key FROM alert_events e JOIN alerts a ON a.id = e.alert_id WHERE e.triggered_at >= ? "
        "ORDER BY e.triggered_at DESC, e.id DESC LIMIT 100",
        (iso(now - timedelta(days=14)),),
    ):
        p = json.loads(r["payload"])
        events.append(
            {
                "key": r["key"],
                "type": p.get("type"),
                "game": p.get("game"),
                "matchup": p.get("matchup"),
                "message": p.get("message"),
                "matches": p.get("matches"),
                "triggered_at": r["triggered_at"],
                "delivery": r["delivery_status"],
                "delivered_at": r["delivered_at"],
            }
        )
    return {"generated_at": iso(now), "delivery": "ntfy" if delivery else None, "alerts": alerts, "events": events}
