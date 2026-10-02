"""Sportsbook names -> NHL ids (docs/04-data-sources.md, entity resolution).

Teams: by club name ("Maple Leafs"), falling back to location ("Utah"). Accents and
punctuation are ignored ("Montréal", "St. Louis").

Players, in order: a stored alias, the owner's config/player_aliases.yml, an exact name match
on the game's two rosters, then a curated nickname rule (Mitch = Mitchell). Anything else is
**unresolved**: recorded in data_quality_issues with a suggested match for the Admin page,
and its lines are not stored, so a line is never attached to the wrong player.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rinkx.config import REPO_ROOT
from rinkx.timeutil import iso, parse_iso

ALIASES_FILE = REPO_ROOT / "config/player_aliases.yml"
MATCH_WINDOW = timedelta(hours=12)  # event start vs. our scheduled start
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
# Curated nickname -> given-name forms seen on sportsbooks.
NICKNAMES = {
    "alex": "alexander",
    "alexei": "alexey",
    "andy": "andrew",
    "ben": "benjamin",
    "bob": "robert",
    "cam": "cameron",
    "chris": "christopher",
    "dan": "daniel",
    "danny": "daniel",
    "dave": "david",
    "ed": "edward",
    "eli": "elias",
    "fred": "frederick",
    "freddie": "frederik",
    "greg": "gregory",
    "jake": "jacob",
    "joe": "joseph",
    "jon": "jonathan",
    "josh": "joshua",
    "kris": "kristopher",
    "matt": "matthew",
    "max": "maxim",
    "mike": "michael",
    "mitch": "mitchell",
    "nate": "nathan",
    "nick": "nicholas",
    "nico": "nicolas",
    "pat": "patrick",
    "rob": "robert",
    "sam": "samuel",
    "steve": "steven",
    "theo": "theodor",
    "tim": "timothy",
    "tom": "thomas",
    "tony": "anthony",
    "vince": "vincent",
    "will": "william",
    "zach": "zachary",
    "zack": "zachary",
}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s)  # curly quotes are already gone after the ASCII fold
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    parts = [p for p in s.split() if p not in SUFFIXES]
    return " ".join(parts)


def _nick(name: str) -> str:
    parts = norm(name).split()
    if parts:
        parts[0] = NICKNAMES.get(parts[0], parts[0])
    return " ".join(parts)


# ---- teams and games ------------------------------------------------------------------------


def team_id(conn: sqlite3.Connection, vendor_name: str) -> int | None:
    v = norm(vendor_name)
    rows = conn.execute("SELECT id, name, location FROM teams WHERE is_active = 1").fetchall()
    by_name = [r[0] for r in rows if r[1] and v.endswith(norm(r[1]))]
    if len(by_name) == 1:
        return int(by_name[0])
    by_loc = [r[0] for r in rows if r[2] and v.startswith(norm(r[2]))]
    return int(by_loc[0]) if len(by_loc) == 1 else None


def match_game(conn: sqlite3.Connection, home: str, away: str, commence: str) -> int | None:
    h, a = team_id(conn, home), team_id(conn, away)
    if h is None or a is None:
        return None
    start = parse_iso(commence)
    rows = conn.execute(
        "SELECT id, start_time_utc FROM games WHERE home_team_id = ? AND away_team_id = ? "
        "AND game_date BETWEEN ? AND ?",
        (h, a, (start - timedelta(days=1)).date().isoformat(), (start + timedelta(days=1)).date().isoformat()),
    ).fetchall()
    best = [(abs(parse_iso(r[1]) - start), r[0]) for r in rows if abs(parse_iso(r[1]) - start) <= MATCH_WINDOW]
    return int(min(best)[1]) if best else None


# ---- players --------------------------------------------------------------------------------


def load_config_aliases(path: Path = ALIASES_FILE) -> dict[str, int]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {norm(k): int(v) for k, v in (data.get("aliases") or {}).items()}


def _candidates(conn: sqlite3.Connection, team_ids: tuple[int, int], as_of: datetime) -> list[sqlite3.Row]:
    """Players on either team now, or who played for either team in the last 30 days (trades)."""
    since = (as_of - timedelta(days=30)).date().isoformat()
    return conn.execute(
        """SELECT DISTINCT p.id, p.nhl_player_id, p.full_name, p.position FROM players p
           WHERE p.current_team_id IN (?, ?)
              OR p.id IN (SELECT s.player_id FROM player_game_stats s JOIN games g ON g.id = s.game_id
                          WHERE s.team_id IN (?, ?) AND g.game_date >= ?
                          UNION SELECT s.player_id FROM goalie_game_stats s JOIN games g ON g.id = s.game_id
                          WHERE s.team_id IN (?, ?) AND g.game_date >= ?)""",
        (*team_ids, *team_ids, since, *team_ids, since),
    ).fetchall()


class PlayerResolver:
    def __init__(self, conn: sqlite3.Connection, source_id: int, now: datetime, config_aliases: dict[str, int]):
        self.conn, self.source_id, self.now, self.config_aliases = conn, source_id, now, config_aliases
        self.unresolved: dict[str, dict[str, Any]] = {}

    def resolve(self, name: str, team_ids: tuple[int, int], game_id: int) -> int | None:
        key = norm(name)
        row = self.conn.execute(
            "SELECT player_id FROM player_aliases WHERE source_id = ? AND alias = ?", (self.source_id, key)
        ).fetchone()
        if row:
            return int(row[0])
        cands = _candidates(self.conn, team_ids, self.now)
        if key in self.config_aliases:
            nhl_id = self.config_aliases[key]
            hit = self.conn.execute("SELECT id FROM players WHERE nhl_player_id = ?", (nhl_id,)).fetchone()
            if hit:
                return self._remember(key, int(hit[0]), "curated")
        exact = [c for c in cands if norm(c["full_name"]) == key]
        if len(exact) == 1:
            return self._remember(key, int(exact[0]["id"]), "exact")
        nick = [c for c in cands if _nick(c["full_name"]) == _nick(name)]
        if len(exact) == 0 and len(nick) == 1:
            return self._remember(key, int(nick[0]["id"]), "curated")
        self._flag(name, key, cands, game_id, "ambiguous" if len(exact) > 1 or len(nick) > 1 else "no_match")
        return None

    def _remember(self, key: str, player_id: int, method: str) -> int:
        self.conn.execute(
            "INSERT OR IGNORE INTO player_aliases (source_id, alias, player_id, method) VALUES (?, ?, ?, ?)",
            (self.source_id, key, player_id, method),
        )
        return player_id

    def _flag(self, name: str, key: str, cands: list[sqlite3.Row], game_id: int, why: str) -> None:
        names = {norm(c["full_name"]): c for c in cands}
        close = difflib.get_close_matches(key, list(names), n=1, cutoff=0.6)
        sugg = names[close[0]] if close else None
        detail = {
            "name": name,
            "reason": why,
            "game_id": game_id,
            "suggestion": {"nhl_id": sugg["nhl_player_id"], "name": sugg["full_name"]} if sugg else None,
        }
        self.unresolved[name] = detail
        open_issue = self.conn.execute(
            "SELECT id FROM data_quality_issues WHERE entity_type = 'odds_player_name' AND entity_id = ? "
            "AND resolved_at IS NULL",
            (key,),
        ).fetchone()
        if open_issue:
            self.conn.execute(
                "UPDATE data_quality_issues SET detail = ? WHERE id = ?", (json.dumps(detail), open_issue[0])
            )
        else:
            self.conn.execute(
                "INSERT INTO data_quality_issues (entity_type, entity_id, issue_code, severity, detail, detected_at) "
                "VALUES ('odds_player_name', ?, 'unresolved_player', 'warn', ?, ?)",
                (key, json.dumps(detail), iso(self.now)),
            )


def resolve_open_issues(conn: sqlite3.Connection, source_id: int, now: datetime) -> int:
    """Close review-queue items whose name now resolves via a stored or configured alias."""
    n = 0
    for issue_id, key in conn.execute(
        "SELECT id, entity_id FROM data_quality_issues WHERE entity_type = 'odds_player_name' AND resolved_at IS NULL"
    ).fetchall():
        if conn.execute("SELECT 1 FROM player_aliases WHERE source_id = ? AND alias = ?", (source_id, key)).fetchone():
            conn.execute("UPDATE data_quality_issues SET resolved_at = ? WHERE id = ?", (iso(now), issue_id))
            n += 1
    return n
