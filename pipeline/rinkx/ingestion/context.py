"""Derived schedule context per team per game: rest, back-to-backs, travel, time zones.

Everything here is computed from the games table plus config/venues.yml, so it is
`derived` data. Inputs that can't be known (first game of a season, neutral-site venues)
produce NULL rather than a guess.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from rinkx.config import REPO_ROOT

VENUES_FILE = REPO_ROOT / "config/venues.yml"
EARTH_RADIUS_KM = 6371.0


def load_venues(path: Path = VENUES_FILE) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    return dict(data["teams"])


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _utc_offset_hours(tz: str, at_utc: str) -> float:
    when = datetime.fromisoformat(at_utc.replace("Z", "+00:00"))
    off = when.astimezone(ZoneInfo(tz)).utcoffset()
    return off.total_seconds() / 3600 if off else 0.0


def recompute_context(conn: sqlite3.Connection, season_id: int) -> int:
    """Rebuild game_team_context for every team-game in a season. Returns rows written."""
    games = conn.execute(
        """SELECT g.id, g.game_date, g.start_time_utc, g.home_team_id, g.away_team_id, g.is_neutral_site,
                  h.venue_lat, h.venue_lon, h.timezone
           FROM games g JOIN teams h ON h.id = g.home_team_id
           WHERE g.season_id = ? AND g.status NOT IN ('postponed', 'cancelled') AND g.game_type IN ('R', 'O')
           ORDER BY g.start_time_utc""",
        (season_id,),
    ).fetchall()

    by_team: dict[int, list[sqlite3.Row]] = {}
    for g in games:
        by_team.setdefault(g["home_team_id"], []).append(g)
        by_team.setdefault(g["away_team_id"], []).append(g)

    def venue(g: sqlite3.Row) -> tuple[float, float, str] | None:
        if g["is_neutral_site"] or g["venue_lat"] is None or g["timezone"] is None:
            return None
        return (g["venue_lat"], g["venue_lon"], g["timezone"])

    conn.execute(
        "DELETE FROM game_team_context WHERE game_id IN (SELECT id FROM games WHERE season_id = ?)", (season_id,)
    )
    rows = 0
    for team, schedule in by_team.items():
        dates = [date.fromisoformat(g["game_date"]) for g in schedule]
        for i, g in enumerate(schedule):
            d = dates[i]
            rest = (d - dates[i - 1]).days - 1 if i > 0 else None
            last7 = sum(1 for prev in dates[:i] if d - timedelta(days=7) <= prev < d)
            travel = tz_shift = None
            if i > 0:
                here, before = venue(g), venue(schedule[i - 1])
                if here and before:
                    travel = round(haversine_km(before[0], before[1], here[0], here[1]), 1)
                    tz_shift = round(
                        _utc_offset_hours(here[2], g["start_time_utc"])
                        - _utc_offset_hours(before[2], schedule[i - 1]["start_time_utc"])
                    )
            conn.execute(
                """INSERT INTO game_team_context (game_id, team_id, is_home, rest_days, is_back_to_back,
                     games_last_7d, travel_km, tz_shift_hours) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    g["id"],
                    team,
                    int(g["home_team_id"] == team),
                    rest,
                    int(rest == 0),
                    last7,
                    travel,
                    tz_shift,
                ),
            )
            rows += 1
    return rows
