"""A synthetic league with known true rates, for model tests only.

Every row is written with provenance='synthetic', so it can never be published (the prod
publish guard refuses any synthetic row). The generator is deliberately simple but has the
structure the models are meant to find: per-player talent, roles (ice time), team defence,
goalie quality, arena scorekeeping bias for hits, and over-dispersion.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from rinkx.config import REPO_ROOT
from rinkx.store.db import connect, migrate

FETCHED = "2026-01-01T00:00:00Z"


@dataclass
class Truth:
    shot_rate: dict[int, float] = field(default_factory=dict)  # per hour, by players.id
    sv: dict[int, float] = field(default_factory=dict)
    team_of: dict[int, int] = field(default_factory=dict)
    goalies: dict[int, list[int]] = field(default_factory=dict)  # team -> [starter, backup]


def build(
    path: str = ":memory:",
    *,
    teams: int = 12,
    days: int = 200,
    seed: int = 1,
    start: date = date(2025, 10, 7),
    season: int = 20252026,
) -> tuple[sqlite3.Connection, Truth]:
    rng = np.random.default_rng(seed)
    conn = connect(path)
    migrate(conn, REPO_ROOT / "db")
    conn.execute(
        "INSERT INTO data_sources (code, name, category, tier, license_notes) VALUES "
        "('synthetic', 'Synthetic test league', 'stats', 'free', 'test data only')"
    )
    src = conn.execute("SELECT id FROM data_sources WHERE code = 'synthetic'").fetchone()[0]
    end = start + timedelta(days=days + 1)
    conn.execute("INSERT INTO seasons VALUES (?, ?, ?)", (season, start.isoformat(), end.isoformat()))
    truth = Truth()

    team_ids = []
    defence: dict[int, float] = {}
    arena_hits: dict[int, float] = {}
    for t in range(teams):
        conn.execute(
            "INSERT INTO teams (nhl_team_id, abbrev, name, location, source_id, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            (100 + t, f"T{t:02d}", f"Team {t}", f"City {t}", src, FETCHED),
        )
        tid = conn.execute("SELECT id FROM teams WHERE nhl_team_id = ?", (100 + t,)).fetchone()[0]
        team_ids.append(tid)
        defence[tid] = float(np.exp(rng.normal(0, 0.12)))  # >1 allows more shots
        arena_hits[tid] = float(np.exp(rng.normal(0, 0.25)))  # scorekeeper bias

    roster: dict[int, list[tuple[int, str, float, float, float, float]]] = {}
    pid = 8_000_000
    for tid in team_ids:
        players = []
        for slot in range(18):
            pos = "D" if slot >= 12 else ("C", "L", "R")[slot % 3]
            role = slot // 3 if pos != "D" else (slot - 12) // 2  # 0 = top line / pair
            toi_min = (19.0 - 2.5 * role) if pos != "D" else (24.0 - 3.0 * role)
            shot_rate = float(rng.gamma(6.0, (9.0 if pos != "D" else 5.0) / 6.0))
            sh_pct = float(rng.beta(12, 100 if pos != "D" else 250))
            hit_rate = float(rng.gamma(3.0, 5.0 / 3.0))
            pid += 1
            conn.execute(
                "INSERT INTO players (nhl_player_id, first_name, last_name, position, current_team_id, source_id, "
                "fetched_at) VALUES (?, 'Syn', ?, ?, ?, ?, ?)",
                (pid, f"P{pid}", pos, tid, src, FETCHED),
            )
            pk = conn.execute("SELECT id FROM players WHERE nhl_player_id = ?", (pid,)).fetchone()[0]
            truth.shot_rate[pk] = shot_rate
            truth.team_of[pk] = tid
            players.append((pk, pos, toi_min, shot_rate, sh_pct, hit_rate))
        roster[tid] = players
        gl = []
        for _ in range(2):
            pid += 1
            conn.execute(
                "INSERT INTO players (nhl_player_id, first_name, last_name, position, current_team_id, source_id, "
                "fetched_at) VALUES (?, 'Syn', ?, 'G', ?, ?, ?)",
                (pid, f"G{pid}", tid, src, FETCHED),
            )
            gk = conn.execute("SELECT id FROM players WHERE nhl_player_id = ?", (pid,)).fetchone()[0]
            truth.sv[gk] = float(np.clip(rng.normal(0.905, 0.012), 0.86, 0.94))
            truth.team_of[gk] = tid
            gl.append(gk)
        truth.goalies[tid] = gl

    game_no = 0
    for d in range(days):
        day = start + timedelta(days=d)
        # half the league plays each day, alternating, in random pairings
        playing = [t for i, t in enumerate(team_ids) if (i + d) % 2 == 0]
        rng.shuffle(playing)
        for i in range(0, len(playing) - 1, 2):
            home, away = playing[i], playing[i + 1]
            game_no += 1
            nhl_id = int(f"{season // 10000}02{game_no:04d}")
            conn.execute(
                "INSERT INTO games (nhl_game_id, season_id, game_type, game_date, start_time_utc, home_team_id, "
                "away_team_id, status, source_id, fetched_at) VALUES (?, ?, 'R', ?, ?, ?, ?, 'final', ?, ?)",
                (nhl_id, season, day.isoformat(), f"{day.isoformat()}T23:00:00Z", home, away, src, FETCHED),
            )
            gid = conn.execute("SELECT id FROM games WHERE nhl_game_id = ?", (nhl_id,)).fetchone()[0]
            lines: dict[int, list[list[float]]] = {}
            starters: dict[int, int] = {}
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                starter = truth.goalies[opp][0 if rng.random() < 0.7 else 1]  # opposing goalie
                starters[opp] = starter
                rows = []
                for pk, pos, toi_min, shot_rate, sh_pct, hit_rate in roster[team]:
                    toi = max(5.0, rng.normal(toi_min, 2.0)) * 60
                    pp = max(0.0, rng.normal(2.0 if toi_min > 17 else 0.3, 0.5)) * 60
                    lam = shot_rate * (toi / 3600) * defence[opp] * (1.03 if is_home else 0.97)
                    shots = int(rng.negative_binomial(8, 8 / (8 + lam)))
                    p_goal = sh_pct * (1 - truth.sv[starter]) / (1 - 0.905)
                    goals = int(rng.binomial(shots, min(p_goal, 0.9)))
                    assists = int(rng.poisson(0.35 * toi / 1200))
                    ppg = int(rng.binomial(goals, min(1.0, pp / max(toi, 1) * 3)))
                    ppa = int(rng.binomial(assists, min(1.0, pp / max(toi, 1) * 3)))
                    hl = hit_rate * (toi / 3600) * arena_hits[home]
                    hits = int(rng.negative_binomial(4, 4 / (4 + hl)))
                    blocks = int(rng.poisson((2.5 if pos == "D" else 1.0) * toi / 3600 * defence[opp] * 1.5))
                    rows.append([pk, toi, pp, goals, assists, ppg, ppa, shots, hits, blocks])
                lines[team] = rows
            score = {t: sum(r[3] for r in rows) for t, rows in lines.items()}
            ended, so_winner, ot_scorer = "REG", None, None
            if score[home] == score[away]:
                winner = home if rng.random() < 0.52 else away
                if rng.random() < 0.6:  # overtime goal
                    ended = "OT"
                    w = np.array([max(r[7], 0.1) for r in lines[winner]])
                    ot_row = lines[winner][int(rng.choice(len(w), p=w / w.sum()))]
                    ot_row[3] += 1
                    ot_row[7] += 1
                    ot_scorer = int(ot_row[0])
                    score[winner] += 1
                else:
                    ended, so_winner = "SO", winner
            events: list[tuple[float, int, int]] = []  # (game second, team, shooter)
            for team, rows in lines.items():
                for r in rows:
                    n_reg = r[3] - (1 if ot_scorer == r[0] else 0)
                    events += [(float(rng.uniform(0, 3600)), team, int(r[0])) for _ in range(n_reg)]
            if ot_scorer is not None:
                events.append((3600 + float(rng.uniform(0, 300)), winner, ot_scorer))
            events.sort()
            for idx, (sec, team, shooter) in enumerate(events):
                period = min(int(sec // 1200) + 1, 4)
                conn.execute(
                    "INSERT INTO pbp_shot_events (game_id, event_idx, period, period_seconds, event_type, shooter_id, "
                    "team_id, source_id, fetched_at) VALUES (?, ?, ?, ?, 'goal', ?, ?, ?, ?)",
                    (gid, idx, period, int(sec - (period - 1) * 1200), shooter, team, src, FETCHED),
                )
            conn.execute("INSERT INTO game_enrichment (game_id, pbp_at) VALUES (?, ?)", (gid, FETCHED))
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                team_shots = 0
                for pk, toi, pp, goals, assists, ppg, ppa, shots, hits, blocks in lines[team]:
                    team_shots += shots
                    conn.execute(
                        "INSERT INTO player_game_stats (player_id, game_id, team_id, opponent_team_id, is_home, toi_s, "
                        "ev_toi_s, pp_toi_s, sh_toi_s, goals, assists, pp_goals, pp_assists, shots, hits, "
                        "blocked_shots, provenance, quality, source_id, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 'synthetic', 1, ?, ?)",
                        (
                            pk,
                            gid,
                            team,
                            opp,
                            is_home,
                            int(toi),
                            int(toi - pp),
                            int(pp),
                            goals,
                            assists,
                            ppg,
                            ppa,
                            shots,
                            hits,
                            blocks,
                            src,
                            FETCHED,
                        ),
                    )
                team_goals = score[team]
                conn.execute(
                    "INSERT INTO goalie_game_stats (player_id, game_id, team_id, opponent_team_id, is_home, started, "
                    "toi_s, shots_against, saves, goals_against, shutout, provenance, quality, source_id, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, 3600, ?, ?, ?, ?, 'synthetic', 1, ?, ?)",
                    (
                        starters[opp],
                        gid,
                        opp,
                        team,
                        1 - is_home,
                        team_shots,
                        team_shots - team_goals,
                        team_goals,
                        int(team_goals == 0),
                        src,
                        FETCHED,
                    ),
                )
                conn.execute(
                    "INSERT INTO team_game_stats (team_id, game_id, opponent_team_id, is_home, goals_for, shots_for, "
                    "provenance, source_id, fetched_at) VALUES (?, ?, ?, ?, ?, ?, 'synthetic', ?, ?)",
                    (team, gid, opp, is_home, team_goals, team_shots, src, FETCHED),
                )
            hs, aw = score[home], score[away]
            if so_winner is not None:  # the NHL adds the shootout "goal" to the final score
                hs, aw = (hs + 1, aw) if so_winner == home else (hs, aw + 1)
            conn.execute(
                "UPDATE games SET home_score = ?, away_score = ?, ended_in = ? WHERE id = ?", (hs, aw, ended, gid)
            )
            winner = home if hs > aw else away
            for t in (home, away):
                dec = "W" if t == winner else ("L" if ended == "REG" else "O")
                conn.execute(
                    "UPDATE goalie_game_stats SET decision = ? WHERE game_id = ? AND team_id = ?", (dec, gid, t)
                )
    conn.commit()
    return conn, truth


UPCOMING_NHL_ID = 2025029999


def add_upcoming(conn: sqlite3.Connection, day: date) -> int:
    """A scheduled game between the first two teams on `day`; the schedule counts as fetched."""
    home, away = (r[0] for r in conn.execute("SELECT id FROM teams ORDER BY id LIMIT 2"))
    src = conn.execute("SELECT id FROM data_sources WHERE code = 'synthetic'").fetchone()[0]
    conn.execute(
        "INSERT INTO games (nhl_game_id, season_id, game_type, game_date, start_time_utc, home_team_id, away_team_id, "
        "venue_name, status, source_id, fetched_at) VALUES (?, 20252026, 'R', ?, ?, ?, ?, 'Arena 0', 'scheduled', "
        "?, ?)",
        (UPCOMING_NHL_ID, day.isoformat(), f"{day.isoformat()}T23:00:00Z", home, away, src, FETCHED),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schedule_coverage (game_date, fetched_at, source_id) VALUES (?, ?, ?)",
        (day.isoformat(), FETCHED, src),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM games WHERE nhl_game_id = ?", (UPCOMING_NHL_ID,)).fetchone()[0])


def _american(p: float) -> int:
    p = min(max(p, 0.03), 0.97)
    return -round(100 * p / (1 - p)) if p >= 0.5 else max(101, round(100 * (1 - p) / p))


def add_priced_history(
    conn: sqlite3.Connection, truth: Truth, *, days: int = 40, per_team: int = 3, seed: int = 3
) -> int:
    """Frozen shots-on-goal predictions for finished games in the last `days` days, as if lines
    had been priced before each game: the model is the player's season-to-date average (Poisson);
    the market is priced from the TRUE rate, with noise and margin, at two books, moving between
    an opening and a closing price. All synthetic. Returns predictions written."""
    import json

    from scipy.stats import poisson

    from rinkx.pricing import confidence as conf
    from rinkx.pricing.model_probs import at_line
    from rinkx.pricing.price import PricingConfig, _insert, price_line
    from rinkx.timeutil import iso, parse_iso

    rng = np.random.default_rng(seed)
    cfg = PricingConfig.load()
    src = conn.execute("SELECT id FROM data_sources WHERE code = 'synthetic'").fetchone()[0]
    market = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    for code, name in (("fanduel", "FanDuel"), ("betmgm", "BetMGM")):
        conn.execute("INSERT OR IGNORE INTO sportsbooks (code, name, is_enabled) VALUES (?, ?, 1)", (code, name))
    books = [r[0] for r in conn.execute("SELECT id FROM sportsbooks WHERE code IN ('fanduel','betmgm') ORDER BY code")]
    conn.execute(
        "INSERT OR IGNORE INTO model_versions (model_family, version, algorithm, feature_list) "
        "VALUES ('skater_shots', 'synthetic', 'season average (synthetic)', '[]')"
    )
    mv = conn.execute("SELECT id FROM model_versions WHERE model_family = 'skater_shots' AND version = 'synthetic'")
    mv_id = mv.fetchone()[0]
    last = conn.execute("SELECT max(game_date) FROM games WHERE status = 'final'").fetchone()[0]
    since = (date.fromisoformat(last) - timedelta(days=days - 1)).isoformat()
    games = conn.execute(
        "SELECT id, start_time_utc, game_date, home_team_id, away_team_id FROM games "
        "WHERE status = 'final' AND game_date >= ? ORDER BY start_time_utc",
        (since,),
    ).fetchall()
    written = 0
    for g in games:
        start = parse_iso(g["start_time_utc"])
        opened, closed = iso(start - timedelta(hours=20)), iso(start - timedelta(hours=1))
        for team in (g["home_team_id"], g["away_team_id"]):
            for (pid,) in conn.execute(
                "SELECT id FROM players WHERE current_team_id = ? AND position <> 'G' ORDER BY id LIMIT ?",
                (team, per_team),
            ).fetchall():
                n, shots, toi = conn.execute(
                    "SELECT count(*), avg(s.shots), avg(s.toi_s) FROM player_game_stats s JOIN games x "
                    "ON x.id = s.game_id WHERE s.player_id = ? AND x.start_time_utc < ?",
                    (pid, g["start_time_utc"]),
                ).fetchone()
                if n < 10:
                    continue
                pmf = [float(v) for v in poisson.pmf(np.arange(25), shots)]
                fair = float(1 - poisson.cdf(2, truth.shot_rate[pid] * toi / 3600))
                proj = conn.execute(
                    "INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, computed_at, "
                    "as_of, mean, pmf, inputs, data_quality, trigger_reason, is_current, provenance) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 0.9, 'scheduled', 0, 'synthetic')",
                    (g["id"], pid, market, mv_id, opened, opened, shots, json.dumps({"p": pmf})),
                ).lastrowid
                for book in books:
                    p_open = float(np.clip(fair + rng.normal(0, 0.025), 0.08, 0.92))
                    p_close = float(np.clip(fair + rng.normal(0, 0.01), 0.08, 0.92))
                    o_over, o_under = _american(p_open + 0.023), _american(1 - p_open + 0.023)
                    c_over, c_under = _american(p_close + 0.023), _american(1 - p_close + 0.023)
                    line_id = conn.execute(
                        "INSERT INTO prop_lines (game_id, market_id, sportsbook_id, player_id, line, over_price, "
                        "under_price, status, first_seen_at, last_seen_at, last_changed_at, provenance, source_id) "
                        "VALUES (?, ?, ?, ?, 2.5, ?, ?, 'closed', ?, ?, ?, 'synthetic', ?)",
                        (g["id"], market, book, pid, c_over, c_under, opened, closed, closed, src),
                    ).lastrowid
                    for at, ov, un in ((opened, o_over, o_under), (closed, c_over, c_under)):
                        conn.execute(
                            "INSERT INTO line_movements (prop_line_id, observed_at, line, over_price, under_price, "
                            "status, source_id) VALUES (?, ?, 2.5, ?, ?, 'open', ?)",
                            (line_id, at, ov, un, src),
                        )
                    pr = price_line("over_under", at_line(pmf, 2.5), 2.5, o_over, o_under, 0.9, cfg, "shots on goal")
                    over_side = pr.side == "over" or (pr.side == "none" and (pr.edge_over or 0) >= (pr.edge_under or 0))
                    c = conf.score(
                        conf.Inputs(
                            edge=(pr.edge_over if over_side else pr.edge_under) or 0.0,
                            p_model=pr.p_over if over_side else pr.p_under,
                            games_in_history=int(n),
                            data_quality=0.9,
                            book_novigs=[],
                            one_sided=False,
                            moved_against_pts=None,
                            is_goalie_prop=False,
                            is_game_market=False,
                            start_probability=None,
                            start_confirmed=False,
                            opp_goalie_confirmed=True,
                        )
                    )
                    row = {
                        "line_id": line_id,
                        "sportsbook_id": book,
                        "line": 2.5,
                        "over_price": o_over,
                        "under_price": o_under,
                    }
                    _insert(
                        conn,
                        proj_col="projection_id",
                        proj_id=proj,
                        r=row,
                        game_id=g["id"],  # type: ignore[arg-type]
                        market_id=market,
                        player_id=pid,
                        pr=pr,
                        c=c,
                        now=parse_iso(opened),
                    )
                    written += 1
    conn.commit()
    return written
