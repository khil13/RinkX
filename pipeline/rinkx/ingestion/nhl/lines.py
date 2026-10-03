"""Line combinations, defence pairs and power-play units from a game's shift chart.

The NHL publishes every shift (player, period, start and end). From those, second by second,
we know who was on the ice together and how many skaters each team had (goalies excluded):

* **Lines and pairs** come from even-strength seconds (5 skaters a side). The forward with the
  most even-strength time seeds a line; his two most frequent forward linemates join him; the
  next unassigned forward seeds the next line, and so on. Lines are numbered by their time on
  ice (F1 = most). Defence pairs are built the same way (D1-D3). A skater who barely played at
  even strength (under MIN_ES_S) is listed as EXTRA.
* **Power-play units** come from seconds when the team had more skaters than the opponent. The
  skater with the most such time seeds PP1, and the four who were out with that group most join
  him; PP2 is built from the rest. A unit needs at least MIN_PP_S of power-play time per player.

This is a reconstruction from who shared the ice, not a coach's lineup card: line juggling
mid-game, injuries and penalties blur it, and it says nothing about the next game. The models use
it only as "his deployment in his most recent game", and test whether that helps.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from rinkx.ingestion.nhl.parse import ParseError, toi_seconds

SHIFT = 517  # typeCode of a shift row (505 rows are goals, listed for the highlight reel)
PERIOD_S = 1200
MIN_ES_S = 120  # even-strength seconds to count as a regular in a line or pair
MIN_PP_S = 30  # power-play seconds to count as a member of a PP unit
F_LINES, D_PAIRS = 4, 3


@dataclass(frozen=True)
class Shift:
    player: int  # NHL player id
    team: int  # NHL team id
    start: int  # game second (period 1 starts at 0)
    end: int


@dataclass
class ShiftChart:
    nhl_game_id: int
    shifts: list[Shift] = field(default_factory=list)


def parse_shift_chart(payload: dict[str, Any], nhl_game_id: int) -> ShiftChart:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ParseError("shift chart has no data list")
    chart = ShiftChart(nhl_game_id)
    for r in rows:
        if r.get("typeCode") != SHIFT:
            continue
        try:
            game, player, team, period = int(r["gameId"]), int(r["playerId"]), int(r["teamId"]), int(r["period"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ParseError(f"shift row {r.get('id')} is missing a field") from exc
        if game != nhl_game_id:
            raise ParseError(f"shift row {r.get('id')} belongs to game {game}, not {nhl_game_id}")
        start, end = toi_seconds(r.get("startTime")), toi_seconds(r.get("endTime"))
        if start is None or end is None:
            continue  # an open shift (game in progress) or a blank row
        if end < start or end > PERIOD_S + 300 * (period > 3) or period < 1:
            raise ParseError(f"shift row {r.get('id')} has impossible times {start}-{end} in period {period}")
        base = (period - 1) * PERIOD_S
        chart.shifts.append(Shift(player, team, base + start, base + end))
    return chart


@dataclass
class Units:
    """One team's deployment: unit -> NHL player ids (F1-F4, D1-D3, EXTRA; PP1, PP2)."""

    team: int
    lines: dict[str, list[int]]
    pp: dict[str, list[int]]
    es_s: dict[int, int]  # even-strength seconds per skater
    pp_s: dict[int, int]


def _occupancy(chart: ShiftChart, skaters: set[int]) -> tuple[dict[int, list[int]], dict[int, np.ndarray], int]:
    """team -> its skaters, skater -> on-ice flag per second, game length in seconds."""
    length = max((s.end for s in chart.shifts), default=0)
    on: dict[int, np.ndarray] = {}
    team_of: dict[int, set[int]] = defaultdict(set)
    for s in chart.shifts:
        if s.player not in skaters:
            continue  # goalies
        a = on.setdefault(s.player, np.zeros(length, dtype=bool))
        a[s.start : s.end] = True
        team_of[s.team].add(s.player)
    return {t: sorted(p) for t, p in team_of.items()}, on, length


def _group(seed: int, pool: list[int], size: int, shared: np.ndarray, idx: dict[int, int]) -> list[int]:
    """The seed plus the `size - 1` players in `pool` who shared the most time with the group."""
    group = [seed]
    rest = [p for p in pool if p != seed]
    while len(group) < size and rest:
        best = max(rest, key=lambda p: (sum(shared[idx[p], idx[g]] for g in group), -p))
        group.append(best)
        rest.remove(best)
    return group


def derive(chart: ShiftChart, positions: dict[int, str]) -> list[Units]:
    """`positions`: NHL player id -> 'F', 'D' or 'G' (players not listed are ignored)."""
    skaters = {p for p, pos in positions.items() if pos in ("F", "D")}
    teams, on, length = _occupancy(chart, skaters)
    if len(teams) != 2 or length == 0:
        return []
    count = {t: sum((on[p] for p in ps), np.zeros(length, dtype=int)) for t, ps in teams.items()}
    t1, t2 = teams
    even = (count[t1] == 5) & (count[t2] == 5)
    out = []
    for team, players in teams.items():
        other = t2 if team == t1 else t1
        pp_mask = (count[team] > count[other]) & (count[other] >= 3) & (count[team] <= 5)  # not 6v5 extra attacker
        idx = {p: i for i, p in enumerate(players)}
        m = np.array([on[p] for p in players], dtype=float)
        es = m * even
        shared_es = es @ es.T
        pp = m * pp_mask
        shared_pp = pp @ pp.T
        es_s = {p: int(shared_es[idx[p], idx[p]]) for p in players}
        pp_s = {p: int(shared_pp[idx[p], idx[p]]) for p in players}
        lines: dict[str, list[int]] = {}
        extra = []
        for pos, n_units, size, prefix in (("F", F_LINES, 3, "F"), ("D", D_PAIRS, 2, "D")):
            pool = sorted((p for p in players if positions[p] == pos and es_s[p] >= MIN_ES_S), key=lambda p: -es_s[p])
            extra += [p for p in players if positions[p] == pos and es_s[p] < MIN_ES_S]
            groups: list[list[int]] = []
            while len(pool) >= size and len(groups) < n_units:
                g = _group(pool[0], pool, size, shared_es, idx)
                groups.append(g)
                pool = [p for p in pool if p not in g]
            extra += pool
            groups.sort(key=lambda g: -sum(es_s[p] for p in g))
            for i, g in enumerate(groups):
                lines[f"{prefix}{i + 1}"] = g
        if extra:
            lines["EXTRA"] = sorted(extra)
        units: dict[str, list[int]] = {}
        pool = sorted((p for p in players if pp_s[p] >= MIN_PP_S), key=lambda p: -pp_s[p])
        for name in ("PP1", "PP2"):
            if not pool:
                break
            g = _group(pool[0], pool, 5, shared_pp, idx)
            units[name] = g
            pool = [p for p in pool if p not in g]
        out.append(Units(team, lines, units, es_s, pp_s))
    return out


# ---- storage ----------------------------------------------------------------------------------

SLOT = {"C": "C", "L": "LW", "R": "RW"}


def write_lines(conn: Any, game_id: int, chart: ShiftChart, source_id: int, fetched_at: str, source_ref: str) -> int:
    """Derive and store this game's lines and PP units (replacing any earlier derivation).
    Returns the number of players placed. Every skater must already be loaded (box score)."""
    from rinkx.ingestion.nhl.store import player_id, team_id

    nhl_ids = {s.player for s in chart.shifts}
    pk: dict[int, int] = {}
    position: dict[int, str] = {}
    for nhl in nhl_ids:
        found = player_id(conn, nhl)
        if found is None:
            raise KeyError(f"player {nhl} in the shift chart is not loaded")
        pk[nhl] = found
        position[nhl] = conn.execute("SELECT position FROM players WHERE id = ?", (found,)).fetchone()[0]
    groups = {p: ("G" if pos == "G" else "D" if pos == "D" else "F") for p, pos in position.items()}
    start = conn.execute("SELECT start_time_utc FROM games WHERE id = ?", (game_id,)).fetchone()[0]
    conn.execute(
        "DELETE FROM lineup_snapshots WHERE game_id = ? AND status = 'actual' AND provenance = 'derived'", (game_id,)
    )
    placed = 0
    for u in derive(chart, groups):
        snap = conn.execute(
            "INSERT INTO lineup_snapshots (team_id, game_id, status, observed_at, provenance, source_id, source_ref, "
            "fetched_at) VALUES (?, ?, 'actual', ?, 'derived', ?, ?, ?)",
            (team_id(conn, u.team), game_id, start, source_id, source_ref, fetched_at),
        ).lastrowid
        for unit, members in u.lines.items():
            used: set[str] = set()
            for nhl in members:
                slot = SLOT.get(position[nhl], "X") if unit.startswith("F") else "X"
                if slot in used:
                    slot = "X"
                used.add(slot)
                conn.execute(
                    "INSERT INTO line_combinations (snapshot_id, unit, slot, player_id) VALUES (?, ?, ?, ?)",
                    (snap, unit, slot, pk[nhl]),
                )
                placed += 1
        for unit, members in u.pp.items():
            for i, nhl in enumerate(members):
                conn.execute(
                    "INSERT INTO powerplay_units (snapshot_id, unit, slot, player_id) VALUES (?, ?, ?, ?)",
                    (snap, unit, i + 1, pk[nhl]),
                )
    conn.execute(
        "INSERT INTO game_enrichment (game_id, shifts_at) VALUES (?, ?) "
        "ON CONFLICT(game_id) DO UPDATE SET shifts_at = excluded.shifts_at",
        (game_id, fetched_at),
    )
    return placed
