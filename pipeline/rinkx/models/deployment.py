"""Deployment tracker: each skater's line, PP unit and ice time for upcoming games, and what changed.

Only verified data counts as a change:
* lines and PP units actually played, reconstructed from NHL shift charts (status 'actual');
* a Quick Entry line change for the upcoming game, with its source (status 'confirmed');
* the projection's expected ice time vs what he played in his last 5 games.

"Current" for an upcoming game is the Quick Entry if there is one, otherwise his last game's. A
change is reported against the game before it (his previous game with line data). Nothing is
inferred from news text, odds or anything else.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from rinkx.models.project import confirmed_lineup
from rinkx.timeutil import iso

TOI_DOWN_S = 120  # expected TOI this far below his last-5 average counts as "expected TOI down"
TOP = ("F1", "D1")
CHANGE_TEXT = {
    "pp1_promotion": "🔥 MOVED TO PP1",
    "top_line_promotion": "🔥 PROMOTED TO TOP LINE",
    "pp1_demotion": "⚠️ DROPPED FROM PP1",
    "top_line_demotion": "⚠️ DROPPED FROM TOP LINE",
    "toi_down": "⚠️ EXPECTED TOI DOWN",
    "toi_up": "🔥 EXPECTED TOI UP",
}


def _actual(conn: sqlite3.Connection, player: int, before: str, limit: int = 2) -> list[dict[str, Any]]:
    """His last `limit` games with line data (most recent first): unit, PP unit, TOI, date, source."""
    rows = conn.execute(
        "SELECT g.id, g.game_date, g.start_time_utc, s.id AS snap, s.source_ref, "
        "(SELECT c.unit FROM line_combinations c WHERE c.snapshot_id = s.id AND c.player_id = ?) AS unit, "
        "(SELECT u.unit FROM powerplay_units u WHERE u.snapshot_id = s.id AND u.player_id = ? "
        " AND u.unit IN ('PP1','PP2')) AS pp, "
        "(SELECT st.toi_s FROM player_game_stats st WHERE st.game_id = g.id AND st.player_id = ?) AS toi "
        "FROM lineup_snapshots s JOIN games g ON g.id = s.game_id WHERE s.status = 'actual' "
        "AND g.start_time_utc < ? AND EXISTS (SELECT 1 FROM line_combinations c2 WHERE c2.snapshot_id = s.id "
        "AND c2.player_id = ?) ORDER BY g.start_time_utc DESC LIMIT ?",
        (player, player, player, before, player, limit),
    ).fetchall()
    return [
        {"date": r["game_date"], "unit": r["unit"], "pp": int(r["pp"][2]) if r["pp"] else None, "toi_s": r["toi"],
         "source": r["source_ref"]}
        for r in rows
    ]  # fmt: skip


def recent_toi(conn: sqlite3.Connection, player: int, before: str, n: int = 5) -> float | None:
    vals = [
        r[0]
        for r in conn.execute(
            "SELECT s.toi_s FROM player_game_stats s JOIN games g ON g.id = s.game_id WHERE s.player_id = ? "
            "AND g.start_time_utc < ? AND g.status = 'final' AND s.toi_s > 0 ORDER BY g.start_time_utc DESC LIMIT ?",
            (player, before, n),
        )
    ]
    return sum(vals) / len(vals) if len(vals) >= 3 else None


def changes(prev: dict[str, Any], cur: dict[str, Any]) -> list[str]:
    """Change kinds from one deployment to the next (unit / pp keys)."""
    out = []
    if cur.get("pp") == 1 and prev.get("pp") != 1:
        out.append("pp1_promotion")
    if prev.get("pp") == 1 and cur.get("pp") != 1 and "pp" in cur:
        out.append("pp1_demotion")
    if cur.get("unit") in TOP and prev.get("unit") not in TOP and prev.get("unit"):
        out.append("top_line_promotion")
    if prev.get("unit") in TOP and cur.get("unit") and cur.get("unit") not in TOP:
        out.append("top_line_demotion")
    return out


def game_deployment(conn: sqlite3.Connection, game: sqlite3.Row) -> list[dict[str, Any]]:
    """Every skater with a current projection for this upcoming game: deployment now and before."""
    out = []
    for team in (game["away_team_id"], game["home_team_id"]):
        qe = confirmed_lineup(conn, game["id"], team)
        abbrev = conn.execute("SELECT abbrev FROM teams WHERE id = ?", (team,)).fetchone()[0]
        players = conn.execute(
            "SELECT DISTINCT p.id, p.nhl_player_id, p.full_name, p.position FROM player_projections pp "
            "JOIN players p ON p.id = pp.player_id WHERE pp.game_id = ? AND pp.is_current = 1 "
            "AND p.current_team_id = ? AND p.position <> 'G' ORDER BY p.full_name",
            (game["id"], team),
        ).fetchall()
        for p in players:
            hist = _actual(conn, p["id"], game["start_time_utc"])
            last = hist[0] if hist else None
            before = hist[1] if len(hist) > 1 else None
            entry = qe.units.get(p["id"]) if qe else None
            cur: dict[str, Any]
            status = "last_game"
            source = last["source"] if last else None
            if entry:
                cur = {"unit": entry.get("unit", last["unit"] if last else None)}
                cur["pp"] = entry["pp"] if "pp" in entry else (last["pp"] if last else None)
                status, source, prev = "quick_entry", qe.source_ref if qe else None, last
            elif last:
                cur, prev = {"unit": last["unit"], "pp": last["pp"]}, before
            else:
                cur, prev = {}, None
            kinds = changes(prev, cur) if prev else []
            proj = conn.execute(
                "SELECT expected_toi_s, expected_pp_toi_s FROM player_projections WHERE game_id = ? AND player_id = ? "
                "AND is_current = 1 AND expected_toi_s IS NOT NULL LIMIT 1",
                (game["id"], p["id"]),
            ).fetchone()
            exp_toi = proj["expected_toi_s"] if proj else None
            rec = recent_toi(conn, p["id"], game["start_time_utc"])
            if exp_toi is not None and rec is not None:
                if exp_toi <= rec - TOI_DOWN_S:
                    kinds.append("toi_down")
                elif exp_toi >= rec + TOI_DOWN_S:
                    kinds.append("toi_up")
            out.append(
                {
                    "player_pk": p["id"],
                    "id": p["nhl_player_id"],
                    "name": p["full_name"],
                    "position": p["position"],
                    "team": abbrev,
                    "line": cur.get("unit"),
                    "pp_unit": cur.get("pp"),
                    "previous": {"line": prev.get("unit"), "pp_unit": prev.get("pp"), "date": prev.get("date")}
                    if prev
                    else None,
                    "status": status,
                    "source": source,
                    "expected_toi_s": exp_toi,
                    "expected_pp_toi_s": proj["expected_pp_toi_s"] if proj else None,
                    "recent_toi_s": round(rec) if rec is not None else None,
                    "changes": kinds,
                }
            )
    return out


def published(conn: sqlite3.Connection, now: datetime, today: str) -> dict[str, Any]:
    """deployment.json: upcoming games' deployment and the changes RinkX can verify."""
    games = conn.execute(
        "SELECT g.*, h.abbrev AS home, a.abbrev AS away FROM games g JOIN teams h ON h.id = g.home_team_id "
        "JOIN teams a ON a.id = g.away_team_id WHERE g.status IN ('scheduled','pregame') AND g.game_date >= ? "
        "AND EXISTS (SELECT 1 FROM player_projections p WHERE p.game_id = g.id AND p.is_current = 1) "
        "ORDER BY g.start_time_utc",
        (today,),
    ).fetchall()
    out = []
    for g in games:
        players = game_deployment(conn, g)
        for p in players:
            p.pop("player_pk")
        out.append(
            {
                "game": {
                    "id": g["nhl_game_id"],
                    "home": g["home"],
                    "away": g["away"],
                    "start_time_utc": g["start_time_utc"],
                    "date": g["game_date"],
                },
                "players": players,
            }
        )
    have_lines = conn.execute("SELECT max(shifts_at) FROM game_enrichment").fetchone()[0]
    return {"generated_at": iso(now), "lines_updated_at": have_lines, "change_text": CHANGE_TEXT, "games": out}


def alert_lines(
    conn: sqlite3.Connection, game: sqlite3.Row, kinds: set[str], player: int | None, team: int | None
) -> list[str]:
    out = []
    for p in game_deployment(conn, game):
        if player is not None and p["player_pk"] != player:
            continue
        if (
            team is not None
            and conn.execute("SELECT abbrev FROM teams WHERE id = ?", (team,)).fetchone()[0] != p["team"]
        ):
            continue
        for k in p["changes"]:
            if kinds and k not in kinds:
                continue
            detail = {
                "toi_down": f"expected {_mmss(p['expected_toi_s'])} vs {_mmss(p['recent_toi_s'])} last 5",
                "toi_up": f"expected {_mmss(p['expected_toi_s'])} vs {_mmss(p['recent_toi_s'])} last 5",
            }.get(k, f"{_was(p)} → now {_slot(p.get('line'), p.get('pp_unit'))}")
            src = "Quick Entry" if p["status"] == "quick_entry" else "last game's shift chart"
            out.append(f"{CHANGE_TEXT[k]}: {p['name']} ({p['team']}), {detail} ({src})")
    return out


def _slot(line: str | None, pp: int | None) -> str:
    return (line or "—") + (f" / PP{pp}" if pp else "")


def _was(p: dict[str, Any]) -> str:
    prev = p.get("previous") or {}
    return f"was {_slot(prev.get('line'), prev.get('pp_unit'))}"


def _mmss(s: float | None) -> str:
    if s is None:
        return "—"
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def projection_changes(conn: sqlite3.Connection, game_id: int, min_pct: float, direction: str) -> list[dict[str, Any]]:
    """Current projections that moved at least min_pct% from the projection they replaced."""
    rows = conn.execute(
        "SELECT p.player_id, pl.full_name, pl.nhl_player_id, m.code, m.name, p.mean, old.mean AS prev, "
        "p.trigger_reason, t.abbrev FROM player_projections p JOIN player_projections old ON old.id = p.supersedes "
        "JOIN players pl ON pl.id = p.player_id JOIN markets m ON m.id = p.market_id "
        "LEFT JOIN teams t ON t.id = pl.current_team_id WHERE p.game_id = ? AND p.is_current = 1 AND old.mean > 0",
        (game_id,),
    ).fetchall()
    out = []
    for r in rows:
        pct = (r["mean"] / r["prev"] - 1) * 100
        if abs(pct) < min_pct or (direction == "up" and pct < 0) or (direction == "down" and pct > 0):
            continue
        out.append({**dict(r), "pct": pct})
    return out
