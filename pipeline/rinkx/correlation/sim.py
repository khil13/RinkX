"""Same-game simulation, for parlays with several legs in one game (reference for web/src/lib/sim.ts).

One simulated game:

1. Regulation goals for each team are drawn from the game model (the same team goal distributions
   the moneyline and totals use). A tie goes to overtime with the game model's OT rate (a goal,
   split by the teams' expected goals) or a shootout (home share from past games).
2. Each goal gets a scorer, drawn in proportion to the skaters' projected goals, then 0, 1 or 2
   assists (shares from play-by-play), drawn in proportion to projected assists. A scorer's own
   linemates are `boost` times as likely to assist as their projections alone say; `boost` is
   estimated from who actually assisted on past goals (1 = no linemate effect).
3. Each skater's shots are his goals plus non-goal shots, drawn around his projection times a team
   pace that is shared by the whole team that night (the goalie model's shots-against spread).
   The opposing goalie's saves are those shots minus goals; goals against are the goals.

So legs in one game move together the way the game does: a big night for a line lifts every
linemate's points, more shots mean more saves for the goalie facing them, and a team that scores
more is more likely to win.

The simulation is used only for *how legs move together*. A parlay's probability is the product of
each leg's own (published) model probability times the simulation's lift:

    P(all) = prod p_model_i * P_sim(all) / prod P_sim(i)

Everything random comes from a seeded 32-bit generator (mulberry32) and pre-computed probability
tables, so the browser reproduces this module exactly; fixtures/sim_vectors.json checks that.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scipy import stats

from rinkx.models import dist

M32 = 0xFFFFFFFF
N_SIMS = 20000
PACE_LEVELS = 5
MIN_GOALS = 300  # play-by-play goals before assist shares are taken from them
MIN_ASSISTS = 300  # assists (with known lines) before a linemate boost is estimated
BOOST_GRID = tuple(round(0.5 + 0.05 * i, 2) for i in range(151))  # 0.5 .. 8.0
DIGITS = 6

# Prop markets the simulation can settle, and what each reads.
SIM_MARKETS = {
    "skater_shots_on_goal": "shots",
    "skater_goals": "goals",
    "skater_anytime_goal": "goals",
    "skater_assists": "assists",
    "skater_points": "points",
    "skater_first_goal": "first_goal",
    "goalie_saves": "saves",
    "goalie_goals_against": "goals_against",
    "goalie_win": "win",
    "goalie_shutout": "shutout",
    "goalie_saves_and_win": "saves_win",
    "game_moneyline": "moneyline",
    "game_total": "game_total",
}


class Rng:
    """mulberry32: the same 32-bit integer steps as the JavaScript version."""

    def __init__(self, seed: int) -> None:
        self.a = seed & M32

    def next(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & M32
        t = self.a
        t = ((t ^ (t >> 15)) * (t | 1)) & M32
        t = ((t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32) ^ t
        return ((t ^ (t >> 14)) & M32) / 4294967296


def cumulative(w: list[float]) -> list[float]:
    out, s = [], 0.0
    for x in w:
        s += x
        out.append(s)
    return out


def pick(cum: list[float], u: float) -> int:
    """First index whose cumulative weight exceeds u * total (binary search)."""
    x = u * cum[-1]
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] > x:
            hi = mid
        else:
            lo = mid + 1
    return lo


# ---- inputs ------------------------------------------------------------------------------------


def _r(x: float) -> float:
    return round(float(x), DIGITS)


def _pmf(mu: float, size: float | None) -> list[float]:
    p = dist.pmf_vector(max(mu, dist.MIN_MU), math.inf if size is None else size, tail=1e-6)
    return [_r(x) for x in p]


def pace_levels(size: float | None) -> list[float]:
    """Team pace multipliers (equally likely): the middles of PACE_LEVELS equal-probability slices of
    Gamma(size, 1/size), rescaled to average 1. Poisson shots against (size None): no pace spread."""
    if size is None:
        return [1.0]
    qs = [(i + 0.5) / PACE_LEVELS for i in range(PACE_LEVELS)]
    v = [float(stats.gamma.ppf(q, size, scale=1 / size)) for q in qs]
    m = sum(v) / len(v)
    return [_r(x / m) for x in v]


@dataclass
class SimSkater:
    id: int  # NHL player id
    side: str  # 'home' | 'away'
    goals: float  # projected mean
    assists: float
    shots: float
    unit: str | None


def build_inputs(
    *,
    game_id: int,
    home: str,
    away: str,
    lam_home: float,
    lam_away: float,
    goal_size: float | None,
    ot_q: float,
    so_home: float,
    playoff: bool,
    pace_size: float | None,
    skaters: list[SimSkater],
    goalies: dict[str, int | None],
    assists_per_goal: list[float],
    boost: float,
    sources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pace = pace_levels(pace_size)
    mates: dict[str, list[int]] = {}
    for s in skaters:
        if s.unit:
            m = [o.id for o in skaters if o.side == s.side and o.unit == s.unit and o.id != s.id]
            if m:
                mates[str(s.id)] = m
    return {
        "version": 1,
        "game_id": game_id,
        "home": home,
        "away": away,
        "goals": {"home": _pmf(lam_home, goal_size), "away": _pmf(lam_away, goal_size)},
        "s_home": _r(lam_home / (lam_home + lam_away)),
        "ot_q": _r(ot_q),
        "so_home": _r(so_home),
        "playoff": playoff,
        "pace": pace,
        "assists_per_goal": [_r(x) for x in assists_per_goal],
        "boost": _r(boost),
        "skaters": [
            {
                "id": s.id,
                "side": s.side,
                "g": _r(max(s.goals, 1e-4)),
                "a": _r(max(s.assists, 1e-4)),
                "shots": [_pmf(max(s.shots - s.goals, 0.01) * p, None) for p in pace],
            }
            for s in skaters
        ],
        "mates": mates,
        "goalies": goalies,
        "sources": sources or {},
    }


# ---- simulation ----------------------------------------------------------------------------------


@dataclass
class Game:
    goals: dict[int, int]
    assists: dict[int, int]
    shots: dict[int, int]
    team_goals: dict[str, int]  # regulation + OT goal (never the shootout)
    team_shots: dict[str, int]
    winner: str
    shootout: bool
    first_scorer: int | None


class Sim:
    def __init__(self, inputs: dict[str, Any]) -> None:
        self.x = inputs
        self.cum_goals = {s: cumulative(inputs["goals"][s]) for s in ("home", "away")}
        self.cum_apg = cumulative(inputs["assists_per_goal"])
        self.sk = inputs["skaters"]
        self.side_idx = {s: [i for i, k in enumerate(self.sk) if k["side"] == s] for s in ("home", "away")}
        self.cum_scorer = {s: cumulative([self.sk[i]["g"] for i in idx]) for s, idx in self.side_idx.items()}
        self.cum_shots = [[cumulative(p) for p in k["shots"]] for k in self.sk]
        self.mates = {int(k): set(v) for k, v in inputs["mates"].items()}
        self.n_pace = len(inputs["pace"])

    def _assister(self, rng: Rng, side: str, scorer: int, taken: tuple[int, ...]) -> int | None:
        idx = [i for i in self.side_idx[side] if self.sk[i]["id"] != scorer and self.sk[i]["id"] not in taken]
        if not idx:
            return None
        mates = self.mates.get(scorer, set())
        b = self.x["boost"]
        w = [self.sk[i]["a"] * (b if self.sk[i]["id"] in mates else 1.0) for i in idx]
        return int(self.sk[idx[pick(cumulative(w), rng.next())]]["id"])

    def _goal(self, rng: Rng, side: str, g: Game) -> int:
        idx = self.side_idx[side]
        scorer = int(self.sk[idx[pick(self.cum_scorer[side], rng.next())]]["id"])
        g.goals[scorer] = g.goals.get(scorer, 0) + 1
        n_ast = pick(self.cum_apg, rng.next())
        taken: tuple[int, ...] = ()
        for _ in range(n_ast):
            a = self._assister(rng, side, scorer, taken)
            if a is None:
                break
            g.assists[a] = g.assists.get(a, 0) + 1
            taken = (*taken, a)
        return scorer

    def one(self, rng: Rng) -> Game:
        x = self.x
        pace = {s: int(rng.next() * self.n_pace) for s in ("home", "away")}
        reg = {s: pick(self.cum_goals[s], rng.next()) for s in ("home", "away")}
        ot: str | None = None
        shootout = False
        if reg["home"] == reg["away"]:
            if x["playoff"] or rng.next() < x["ot_q"]:
                ot = "home" if rng.next() < x["s_home"] else "away"
            else:
                shootout = True
        g = Game({}, {}, {}, {}, {}, "home", shootout, None)
        scorers: list[int] = []
        for side in ("home", "away"):
            for _ in range(reg[side]):
                scorers.append(self._goal(rng, side, g))
        ot_scorer = self._goal(rng, ot, g) if ot is not None else None
        if scorers:
            g.first_scorer = scorers[int(rng.next() * len(scorers))]
        else:
            g.first_scorer = ot_scorer
        for s in ("home", "away"):
            g.team_goals[s] = reg[s] + (1 if ot == s else 0)
        for i, k in enumerate(self.sk):
            pid = int(k["id"])
            g.shots[pid] = g.goals.get(pid, 0) + pick(self.cum_shots[i][pace[k["side"]]], rng.next())
        for s in ("home", "away"):
            g.team_shots[s] = sum(g.shots[int(self.sk[i]["id"])] for i in self.side_idx[s])
        if shootout:
            g.winner = "home" if rng.next() < x["so_home"] else "away"
        else:
            g.winner = "home" if g.team_goals["home"] > g.team_goals["away"] else "away"
        return g


def _side_of_goalie(inputs: dict[str, Any], player: int | None) -> str | None:
    for s in ("home", "away"):
        if player is not None and inputs["goalies"].get(s) == player:
            return s
    return None


def supported(inputs: dict[str, Any], leg: dict[str, Any]) -> bool:
    kind = SIM_MARKETS.get(leg["market"])
    if kind is None:
        return False
    if kind in ("moneyline", "game_total"):
        return True
    if kind in ("saves", "goals_against", "win", "shutout", "saves_win"):
        return _side_of_goalie(inputs, leg.get("player")) is not None
    return any(int(k["id"]) == leg.get("player") for k in inputs["skaters"])


def outcome(inputs: dict[str, Any], leg: dict[str, Any], g: Game) -> bool:
    """Did this leg win in this simulated game? (A push counts as not winning.)"""
    kind = SIM_MARKETS[leg["market"]]
    side: str = leg["side"]
    line: float | None = leg.get("line")
    pid: int | None = leg.get("player")
    if kind == "moneyline":
        return g.winner == side
    if kind == "game_total":
        v = float(g.team_goals["home"] + g.team_goals["away"] + (1 if g.shootout else 0))
    elif kind in ("saves", "goals_against", "win", "shutout", "saves_win"):
        me = _side_of_goalie(inputs, pid)
        assert me is not None
        opp = "away" if me == "home" else "home"
        ga = g.team_goals[opp]
        if kind == "win":
            return (g.winner == me) == (side == "yes")
        if kind == "shutout":
            return (ga == 0) == (side == "yes")
        saves = g.team_shots[opp] - ga
        if kind == "saves_win":
            hit = g.winner == me and line is not None and saves > line
            return hit == (side == "yes")
        v = float(saves if kind == "saves" else ga)
    elif kind == "first_goal":
        return (g.first_scorer == pid) == (side == "yes")
    else:
        assert pid is not None
        gl, a = g.goals.get(pid, 0), g.assists.get(pid, 0)
        v = float({"shots": g.shots.get(pid, 0), "goals": gl, "assists": a, "points": gl + a}[kind])
    if side in ("yes", "no"):
        return (v >= 1) == (side == "yes")
    assert line is not None
    return v > line if side == "over" else v < line


def simulate(inputs: dict[str, Any], legs: list[dict[str, Any]], n: int = N_SIMS, seed: int = 1) -> dict[str, Any]:
    """Win counts for each leg and for all legs together, over n simulated games."""
    sim = Sim(inputs)
    rng = Rng(seed)
    each = [0] * len(legs)
    both = 0
    for _ in range(n):
        g = sim.one(rng)
        ok = [outcome(inputs, leg, g) for leg in legs]
        for i, o in enumerate(ok):
            each[i] += o
        both += all(ok)
    return {"n": n, "each": each, "all": both}


# ---- estimated parameters --------------------------------------------------------------------------


def assist_model(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many assists a goal gets, and how much more likely the scorer's linemates are to get them."""
    counts = [0, 0, 0]
    for n_a, c in conn.execute(
        "SELECT (e.assist1_id IS NOT NULL) + (e.assist2_id IS NOT NULL), count(*) FROM pbp_shot_events e "
        "JOIN game_enrichment ge ON ge.game_id = e.game_id WHERE e.event_type = 'goal' AND ge.pbp_version >= 2 "
        "GROUP BY 1"
    ):
        counts[int(n_a)] += int(c)
    n_goals = sum(counts)
    if n_goals >= MIN_GOALS:
        per_goal = [c / n_goals for c in counts]
        apg_source = "play_by_play"
    else:  # from season totals: assists per goal a, between 1 and 2, as P(1) = 2 - a, P(2) = a - 1
        g, a = conn.execute("SELECT sum(goals), sum(assists) FROM player_game_stats").fetchone()
        ratio = min(max((a or 0) / g, 1.0), 2.0) if g else 1.7
        per_goal = [0.0, 2 - ratio, ratio - 1]
        apg_source = "season_totals"
    boost, n_ast = linemate_boost(conn)
    return {
        "assists_per_goal": per_goal,
        "assists_per_goal_source": apg_source,
        "goals": n_goals,
        "boost": boost,
        "boost_assists": n_ast,
        "boost_source": "play_by_play" if n_ast >= MIN_ASSISTS else "not_enough_data",
    }


def linemate_boost(conn: sqlite3.Connection) -> tuple[float, int]:
    """Maximum-likelihood linemate boost from assisted goals in games with lines (1.0 below MIN_ASSISTS).
    Each assist is a choice among the scorer's dressed teammates, weighted by their assists per game."""
    rate = {
        r[0]: r[1]
        for r in conn.execute("SELECT player_id, avg(assists) FROM player_game_stats GROUP BY player_id").fetchall()
    }
    units: dict[tuple[int, int], str] = {}
    for gid, pid, unit in conn.execute(
        "SELECT s.game_id, c.player_id, c.unit FROM lineup_snapshots s JOIN line_combinations c "
        "ON c.snapshot_id = s.id WHERE s.status = 'actual'"
    ):
        units[(gid, pid)] = unit
    dressed: dict[tuple[int, int], list[int]] = defaultdict(list)
    for gid, team, pid in conn.execute("SELECT game_id, team_id, player_id FROM player_game_stats"):
        dressed[(gid, team)].append(pid)
    choices: list[tuple[float, float, int]] = []  # (mates weight, others weight, chose a mate)
    for gid, team, scorer, a1, a2 in conn.execute(
        "SELECT e.game_id, e.team_id, e.shooter_id, e.assist1_id, e.assist2_id FROM pbp_shot_events e "
        "WHERE e.event_type = 'goal' AND e.assist1_id IS NOT NULL"
    ):
        su = units.get((gid, scorer))
        if su is None or su[0] not in "FD":
            continue
        taken = {scorer}
        for a in (a1, a2):
            if a is None:
                break
            pool = [p for p in dressed[(gid, team)] if p not in taken]
            if a not in pool:
                break
            wm = sum(rate.get(p) or 0.01 for p in pool if units.get((gid, p)) == su)
            wo = sum(rate.get(p) or 0.01 for p in pool if units.get((gid, p)) != su)
            if wm > 0 and wo > 0:
                choices.append((wm, wo, int(units.get((gid, a)) == su)))
            taken.add(a)
    if len(choices) < MIN_ASSISTS:
        return 1.0, len(choices)

    def ll(b: float) -> float:
        return sum(math.log(b * wm / (b * wm + wo)) if y else math.log(wo / (b * wm + wo)) for wm, wo, y in choices)

    return max(BOOST_GRID, key=ll), len(choices)


# ---- inputs for an upcoming game, from the published projections ----------------------------------


def game_inputs(conn: sqlite3.Connection, game: sqlite3.Row, assists: dict[str, Any]) -> dict[str, Any] | None:
    """Simulation inputs for one upcoming game, or None when the projections it needs aren't published."""
    from rinkx.models import project

    env = conn.execute(
        "SELECT gp.inputs FROM game_projections gp JOIN markets m ON m.id = gp.market_id WHERE gp.game_id = ? "
        "AND gp.is_current = 1 AND m.code IN ('game_moneyline','game_total') LIMIT 1",
        (game["id"],),
    ).fetchone()
    if env is None:
        return None
    gi = json.loads(env[0])
    report, _ = project.published_report(conn)
    if report is None or "game" not in report["choices"]:
        return None
    g_size = report["choices"]["game"].get("size")
    saves = report["choices"].get("saves")
    pace_size = saves.get("sa_size") if saves else None
    rows = conn.execute(
        "SELECT pl.nhl_player_id, pl.current_team_id, m.code, p.mean, json_extract(p.inputs, '$.line') AS unit "
        "FROM player_projections p JOIN markets m ON m.id = p.market_id JOIN players pl ON pl.id = p.player_id "
        "WHERE p.game_id = ? AND p.is_current = 1 AND m.code IN ('skater_goals','skater_assists',"
        "'skater_shots_on_goal')",
        (game["id"],),
    ).fetchall()
    by: dict[int, dict[str, Any]] = defaultdict(dict)
    for nhl, team, code, mean, unit in rows:
        by[nhl] |= {"team": team, code: mean, "unit": unit}
    need = ("skater_goals", "skater_assists", "skater_shots_on_goal")
    skaters = [
        SimSkater(
            nhl,
            "home" if v["team"] == game["home_team_id"] else "away",
            v["skater_goals"],
            v["skater_assists"],
            v["skater_shots_on_goal"],
            v["unit"],
        )
        for nhl, v in sorted(by.items())
        if all(k in v for k in need)
    ]
    sides = {s.side for s in skaters}
    if sides != {"home", "away"}:
        return None
    goalies: dict[str, int | None] = {}
    for side, team in (("home", game["home_team_id"]), ("away", game["away_team_id"])):
        mix = project.goalie_mix(conn, game["id"], team)
        goalies[side] = (
            conn.execute("SELECT nhl_player_id FROM players WHERE id = ?", (mix.mix[0][0],)).fetchone()[0]
            if mix.mix
            else None
        )
    teams = dict(
        conn.execute("SELECT id, abbrev FROM teams WHERE id IN (?, ?)", (game["home_team_id"], game["away_team_id"]))
    )
    return build_inputs(
        game_id=game["nhl_game_id"],
        home=teams[game["home_team_id"]],
        away=teams[game["away_team_id"]],
        lam_home=gi["expected_goals_home"],
        lam_away=gi["expected_goals_away"],
        goal_size=g_size,
        ot_q=gi["ot_goal_rate"],
        so_home=gi["home_shootout_win_rate"],
        playoff=game["game_type"] == "O",
        pace_size=pace_size,
        skaters=skaters,
        goalies=goalies,
        assists_per_goal=assists["assists_per_goal"],
        boost=assists["boost"],
        sources={k: v for k, v in assists.items() if k not in ("assists_per_goal", "boost")},
    )


# ---- shared test vectors -----------------------------------------------------------------------------


def vector_inputs() -> dict[str, Any]:
    sk = []
    for side in ("home", "away"):  # 12 forwards in four lines, 6 defence in three pairs
        for i in range(18):
            unit = f"F{i // 3 + 1}" if i < 12 else f"D{(i - 12) // 2 + 1}"
            role = i // 3 if i < 12 else (i - 12) // 2
            d = i >= 12
            sk.append(
                SimSkater(
                    100 + i + (0 if side == "home" else 50),
                    side,
                    (0.09 if d else 0.32) - 0.05 * role,
                    (0.28 if d else 0.42) - 0.06 * role,
                    (2.0 if d else 3.0) - 0.45 * role,
                    unit,
                )
            )
    return build_inputs(
        game_id=1,
        home="HOM",
        away="AWY",
        lam_home=3.2,
        lam_away=2.7,
        goal_size=20.0,
        ot_q=0.6,
        so_home=0.52,
        playoff=False,
        pace_size=8.0,
        skaters=sk,
        goalies={"home": 900, "away": 950},
        assists_per_goal=[0.05, 0.25, 0.70],
        boost=2.5,
    )


VECTOR_LEGS: list[list[dict[str, Any]]] = [
    [{"market": "skater_points", "player": 100, "line": 0.5, "side": "over"},
     {"market": "skater_points", "player": 101, "line": 0.5, "side": "over"}],
    [{"market": "skater_shots_on_goal", "player": 150, "line": 2.5, "side": "over"},
     {"market": "goalie_saves", "player": 900, "line": 27.5, "side": "over"}],
    [{"market": "game_moneyline", "side": "home"},
     {"market": "goalie_win", "player": 900, "side": "yes"},
     {"market": "skater_anytime_goal", "player": 100, "side": "yes"}],
    [{"market": "skater_first_goal", "player": 103, "side": "yes"},
     {"market": "game_total", "line": 5.5, "side": "under"},
     {"market": "goalie_shutout", "player": 950, "side": "no"},
     {"market": "goalie_saves_and_win", "player": 950, "line": 24.5, "side": "yes"}],
]  # fmt: skip


def vectors(n: int = 3000, seed: int = 7) -> dict[str, Any]:
    inputs = vector_inputs()
    rng = Rng(12345)
    return {
        "about": "Generated by pipeline/rinkx/correlation/sim.py; web/src/lib/sim.ts must match exactly.",
        "rng": [rng.next() for _ in range(5)],
        "inputs": inputs,
        "n": n,
        "seed": seed,
        "cases": [{"legs": legs, **simulate(inputs, legs, n, seed)} for legs in VECTOR_LEGS],
    }


def write_vectors(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vectors(), indent=1) + "\n")


if __name__ == "__main__":  # python -m rinkx.correlation.sim  (regenerates the shared vectors)
    from rinkx.config import REPO_ROOT

    write_vectors(REPO_ROOT / "fixtures/sim_vectors.json")
