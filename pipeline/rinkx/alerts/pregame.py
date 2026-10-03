"""Pre-game check of the Card of the Day.

Every run, each pick on the card (built exactly as the site builds it, web/src/lib/card.ts) is
remembered as it was when it first made the card: price and line at its best book, the opposing
starting goalie (or, for a goalie prop, his own team's), and the player's line and PP unit. About
an hour before the first game of the day, the `pregame` alert compares every pick with now and
lists what changed: price moved against the pick, line moved, the pick no longer a lean, a
different goalie, a new line or PP unit, or the player ruled out. Nothing changed: no alert.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from rinkx.models import deployment
from rinkx.models.project import goalie_mix
from rinkx.pricing.odds import implied
from rinkx.publish.best import best_props
from rinkx.timeutil import iso, parse_iso

MAX_TEAM = 4  # same limits as the site's card (web/src/lib/card.ts)
PER_GROUP = 3
# web/src/lib/marketGroups.ts, in the same order: player picks fill these groups in turn
GROUPS: tuple[tuple[str, ...], ...] = (
    ("skater_shots_on_goal",),
    ("skater_points", "skater_pp_points"),
    ("skater_goals", "skater_anytime_goal", "skater_first_goal", "skater_pp_goal"),
    ("skater_assists", "skater_pp_assist"),
    ("skater_blocked_shots",),
    ("skater_hits",),
    ("goalie_saves", "goalie_goals_against", "goalie_win", "goalie_shutout", "goalie_saves_and_win"),
)
LATEST_BEFORE = timedelta(minutes=10)  # too close to puck drop to act on: skip


def _key(r: dict[str, Any]) -> str:
    return f"{r['game']['id']}|{r['subject']['name']}|{r['market']}|{r['lean']}"


def card(rows: list[dict[str, Any]], date: str) -> list[dict[str, Any]]:
    """The Card of the Day for `date`, as the site builds it: leans on that date, the best-EV book
    per prop; game props one per game (up to 4) by EV; player props by market group in order, up
    to 3 per group by EV, one pick per player across the card."""
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r["lean"] or r["game"]["date"] != date:
            continue
        k = f"{_key(r)}|{r['line']}"
        if k not in best or (r["ev"] or -9) > (best[k]["ev"] or -9):
            best[k] = r
    by_ev = sorted(best.values(), key=lambda r: -(r["ev"] or -9))
    out: list[dict[str, Any]] = []
    games: set[int] = set()
    for r in by_ev:
        if r["subject"]["type"] == "game" and r["game"]["id"] not in games and len(games) < MAX_TEAM:
            games.add(r["game"]["id"])
            out.append(r)
    used: set[int] = set()
    for markets in GROUPS:
        n = 0
        for r in by_ev:
            if n >= PER_GROUP:
                break
            if r["subject"]["type"] != "player" or r["market"] not in markets or r["subject"]["id"] in used:
                continue
            used.add(r["subject"]["id"])
            out.append(r)
            n += 1
    return out


def _game(conn: sqlite3.Connection, nhl_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT g.*, a.abbrev || ' @ ' || h.abbrev AS matchup FROM games g JOIN teams h ON h.id = g.home_team_id "
        "JOIN teams a ON a.id = g.away_team_id WHERE g.nhl_game_id = ?",
        (nhl_id,),
    ).fetchone()
    assert row is not None
    out: sqlite3.Row = row
    return out


def _player(conn: sqlite3.Connection, nhl_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, full_name, position, current_team_id FROM players WHERE nhl_player_id = ?", (nhl_id,)
    ).fetchone()
    return row


def state(conn: sqlite3.Connection, r: dict[str, Any], deploy: dict[int, dict[str, Any]]) -> dict[str, Any]:
    g = _game(conn, r["game"]["id"])
    s: dict[str, Any] = {"price": r["price"], "line": r["line"], "book": r["book_name"], "label": _label(r)}
    if r["subject"]["type"] != "player":
        return s
    p = _player(conn, r["subject"]["id"])
    if p is None:
        return s
    s["player_pk"], s["game_pk"] = p["id"], g["id"]
    own = p["current_team_id"]
    opp = g["away_team_id"] if own == g["home_team_id"] else g["home_team_id"]
    mix = goalie_mix(conn, g["id"], own if p["position"] == "G" else opp)
    if mix.mix:
        gid = mix.mix[0][0]
        name = conn.execute("SELECT full_name FROM players WHERE id = ?", (gid,)).fetchone()[0]
        s["goalie"] = {"id": gid, "name": name, "status": mix.status}
    d = deploy.get(p["id"])
    if d is not None:
        s["unit"], s["pp"] = d.get("line"), d.get("pp_unit")
    out = conn.execute(
        "SELECT 1 FROM game_availability WHERE game_id = ? AND player_id = ? AND status = 'out'", (g["id"], p["id"])
    ).fetchone()
    s["out"] = out is not None
    return s


def _label(r: dict[str, Any]) -> str:
    side = {"over": "Over", "under": "Under", "yes": "Yes", "no": "No", "home": "", "away": ""}.get(r["lean"], "")
    line = f" {r['line']:g}" if r["line"] is not None else ""
    who = r["subject"]["name"]
    return f"{who} {side}{line} {r['market_label']}".replace("  ", " ")


def _deployments(conn: sqlite3.Connection, picks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for nhl in {r["game"]["id"] for r in picks if r["subject"]["type"] == "player"}:
        for d in deployment.game_deployment(conn, _game(conn, nhl)):
            out[d["player_pk"]] = d
    return out


def record(conn: sqlite3.Connection, now: datetime, rows: list[dict[str, Any]]) -> None:
    """Remember each pick of each upcoming date's card as it was when it first made the card."""
    conn.execute("DELETE FROM card_picks WHERE game_date < ?", ((now - timedelta(days=14)).date().isoformat(),))
    dates = sorted({r["game"]["date"] for r in rows})
    for date in dates:
        picks = card(rows, date)
        known = {k for (k,) in conn.execute("SELECT pick_key FROM card_picks WHERE game_date = ?", (date,)).fetchall()}
        new = [r for r in picks if _key(r) not in known]
        if not new:
            continue
        deploy = _deployments(conn, new)
        for r in new:
            conn.execute(
                "INSERT OR IGNORE INTO card_picks (game_date, pick_key, game_id, first_seen_at, state) "
                "VALUES (?,?,?,?,?)",
                (date, _key(r), _game(conn, r["game"]["id"])["id"], iso(now), json.dumps(state(conn, r, deploy))),
            )


def _slot(unit: str | None, pp: int | None) -> str:
    return f"{unit or '—'}{f' / PP{pp}' if pp else ''}"


def changes(
    was: dict[str, Any], now_r: dict[str, Any] | None, now_s: dict[str, Any] | None, min_pts: float
) -> list[str]:
    """What changed for one pick, in words (empty: nothing worth a message)."""
    out: list[str] = []
    if now_s is not None and now_s.get("out") and not was.get("out"):
        return ["ruled out"]
    if now_r is None:
        out.append("no longer a lean")
        return out
    if now_r["line"] != was.get("line") and was.get("line") is not None:
        out.append(f"line {was['line']:g} → {now_r['line']:g}")
    elif was.get("price") is not None and now_r["price"] is not None:
        moved = implied(now_r["price"]) - implied(was["price"])
        if moved * 100 >= min_pts:
            out.append(f"price {was['price']:+d} → {now_r['price']:+d} ({now_r['book_name']})")
    if now_s is not None:
        g0, g1 = was.get("goalie"), now_s.get("goalie")
        if g0 and g1 and g0["id"] != g1["id"]:
            out.append(f"goalie now {g1['name']} ({g1['status']}), was {g0['name']}")
        before, after = (was.get("unit"), was.get("pp")), (now_s.get("unit"), now_s.get("pp"))
        if "unit" in was and after[0] and before != after:
            out.append(f"now {_slot(*after)}, was {_slot(*before)}")
    return out


def check(
    conn: sqlite3.Connection, now: datetime, minutes_before: float = 60, min_pts: float = 2.0
) -> Iterator[tuple[dict[str, Any], str]]:
    """Inside the window before the first game of a date: one line per changed pick."""
    rows = best_props(conn, now)["rows"]
    record(conn, now, rows)
    for (date,) in conn.execute("SELECT DISTINCT game_date FROM card_picks").fetchall():
        first = conn.execute(
            "SELECT g.*, a.abbrev || ' @ ' || h.abbrev AS matchup FROM games g JOIN teams h ON h.id = g.home_team_id "
            "JOIN teams a ON a.id = g.away_team_id WHERE g.game_date = ? AND g.status IN ('scheduled','pregame') "
            "ORDER BY g.start_time_utc LIMIT 1",
            (date,),
        ).fetchone()
        if first is None:
            continue
        start = parse_iso(first["start_time_utc"])
        # game-day runs come every 10 minutes (pipeline.yml), so the first run in this window is
        # within about 10 minutes of `minutes_before`; the hourly baseline still lands inside it
        if not (start - timedelta(minutes=minutes_before + 10) <= now <= start - LATEST_BEFORE):
            continue
        current = {_key(r): r for r in card(rows, date)}
        # a pick whose line moved is a different card row: find it by game, player, market and side
        anyline: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r["lean"] and r["game"]["date"] == date:
                k = _key(r)
                if k not in anyline or (r["ev"] or -9) > (anyline[k]["ev"] or -9):
                    anyline[k] = r
        picks = conn.execute(
            "SELECT pick_key, state FROM card_picks WHERE game_date = ? ORDER BY id", (date,)
        ).fetchall()
        live = [current.get(k) or anyline.get(k) for k, _ in picks]
        deploy = _deployments(conn, [r for r in live if r is not None])
        game = {"id": first["id"], "nhl_game_id": first["nhl_game_id"], "matchup": f"Card of the Day · {date}"}
        for (k, raw), r in zip(picks, live, strict=True):
            was = json.loads(raw)
            now_s = state(conn, r, deploy) if r is not None else None
            if now_s is None and was.get("player_pk") is not None:  # line pulled: was he ruled out?
                out = conn.execute(
                    "SELECT 1 FROM game_availability WHERE game_id = ? AND player_id = ? AND status = 'out'",
                    (was["game_pk"], was["player_pk"]),
                ).fetchone()
                now_s = {"out": out is not None}
            what = changes(was, r, now_s, min_pts)
            if what:
                yield game, f"{was.get('label', k)}: {'; '.join(what)}"
