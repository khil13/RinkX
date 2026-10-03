"""Point-in-time model state, built by replaying completed games in date order.

`walk()` predicts every game on a date from the state *before* that date, then learns from
the date's results. Features for a game can therefore never see that game or anything after
it; the leakage tests check this. Production projections call the same `skater_features` /
`goalie_features` on the state after the last completed game, so the backtest and the live
site run identical code.

All per-player sums decay per game played (exponential weights, several half-lives tracked
at once so the tuner can pick one per stat). League priors are cumulative.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass, field

from rinkx.models.history import GOALIE_STATS, SKATER_STATS, GameRecord, GoalieLine, SkaterLine, by_date

HALF_LIVES: tuple[float, ...] = (8.0, 20.0, 50.0)  # games played
DECAYS = tuple(0.5 ** (1 / h) for h in HALF_LIVES)
H = len(HALF_LIVES)
TOI_K = 2.0  # expected ice time: shrink toward the position average by this many games
TEAM_DECAY = 0.5 ** (1 / 30)  # team tendencies change slowly
TEAM_C = 10.0  # team factors: shrink toward league average by this many games
VENUE_DECAY = 0.5 ** (1 / 40)
VENUE_C = 15.0
GOALIE_DECAY = 0.5 ** (1 / 60)
K_SV: tuple[float, ...] = (500.0, 1500.0, 4000.0)  # save %: prior strength in shots
PP_STATS = frozenset({"pp_points", "pp_goals", "pp_assists"})
VENUE_STATS = ("hits", "blocks")
POS_TOI_FALLBACK_H = {"F": 15.5 / 60, "D": 21.0 / 60}  # only used before any game is loaded
FIN_C: tuple[float, ...] = (150.0, 500.0, 1500.0)  # team finishing %: prior strength in shots
EVEN_PRIOR = 10.0  # OT/shootout splits: shrink toward 50/50 by this many games
PLAYOFF_K_H = 200.0  # playoff factor: shrink toward "no effect" by this many skater-hours
PLAYOFF_K_GAMES = 40.0  # ...and by this many team-games for team shot volume


def basis_of(stat: str) -> str:
    return "pp" if stat in PP_STATS else "toi"


def _decayed(vec: list[float], decays: tuple[float, ...] = DECAYS) -> None:
    for i, d in enumerate(decays):
        vec[i] *= d


def _zeros() -> list[float]:
    return [0.0] * H


@dataclass
class SkaterState:
    n: list[float] = field(default_factory=_zeros)  # decayed games with known TOI
    toi: list[float] = field(default_factory=_zeros)  # decayed TOI hours
    pn: list[float] = field(default_factory=_zeros)  # decayed games with known PP TOI
    ptoi: list[float] = field(default_factory=_zeros)
    x: dict[str, list[float]] = field(default_factory=lambda: {s: _zeros() for s in SKATER_STATS})
    b: dict[str, list[float]] = field(default_factory=lambda: {s: _zeros() for s in SKATER_STATS})
    games: int = 0
    season: dict[int, dict[str, list[float]]] = field(default_factory=dict)  # season -> stat -> [sum, n]
    recent: dict[str, deque[float]] = field(default_factory=lambda: {s: deque(maxlen=10) for s in SKATER_STATS})
    last_date: str | None = None
    last_team: int | None = None


@dataclass
class GoalieState:
    saves: float = 0.0  # decayed, all appearances
    sa: float = 0.0
    starts: int = 0
    season: dict[int, dict[str, list[float]]] = field(default_factory=dict)  # starts only
    recent: dict[str, deque[float]] = field(default_factory=lambda: {s: deque(maxlen=10) for s in GOALIE_STATS})
    last_team: int | None = None


@dataclass
class PosLeague:
    games: float = 0.0
    toi: float = 0.0
    pgames: float = 0.0
    ptoi: float = 0.0
    x: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))
    b: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))


@dataclass
class TeamState:
    # decayed per-game sums and the decayed count of games each sum covers (per stat: a stat
    # can be unknown for some games, e.g. PP assists before the stats-API enrichment)
    for_: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))
    for_n: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))
    against: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))
    against_n: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))
    nh: float = 0.0  # venue: decayed home games
    nr: float = 0.0
    home_tot: dict[str, float] = field(default_factory=lambda: dict.fromkeys(VENUE_STATS, 0.0))
    road_tot: dict[str, float] = field(default_factory=lambda: dict.fromkeys(VENUE_STATS, 0.0))
    record: dict[int, list[float]] = field(default_factory=dict)  # season -> [games, wins, goals]
    recent_goals: deque[float] = field(default_factory=lambda: deque(maxlen=10))


@dataclass
class League:
    pos: dict[str, PosLeague] = field(default_factory=lambda: {"F": PosLeague(), "D": PosLeague()})
    # home/away rates per stat (all positions): [x_home, b_home, x_away, b_away]
    ha: dict[str, list[float]] = field(default_factory=lambda: {s: [0.0] * 4 for s in SKATER_STATS})
    team_n: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))
    team_tot: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SKATER_STATS, 0.0))
    # playoff vs regular-season rates, all positions: [x_playoff, b_playoff, x_regular, b_regular]
    po: dict[str, list[float]] = field(default_factory=lambda: {s: [0.0] * 4 for s in SKATER_STATS})
    po_shots: list[float] = field(default_factory=lambda: [0.0] * 4)  # team shots: [po_sum, po_n, reg_sum, reg_n]
    saves: float = 0.0
    sa: float = 0.0
    starter_sa: float = 0.0
    starts: float = 0.0
    shutouts: float = 0.0  # starts that were shutouts
    so_known: float = 0.0  # starts with a known shutout flag
    games: float = 0.0  # final scores known
    home_wins: float = 0.0
    tied_reg: float = 0.0  # regular-season games tied after regulation
    ended_ot: float = 0.0
    so_home_wins: float = 0.0


@dataclass(frozen=True)
class Row:
    """One prediction: who/when, the features, and (in the backtest) what actually happened."""

    kind: str  # 'skater' | 'goalie'
    game_id: int
    date: str
    season: int
    player: int
    team: int
    opp: int
    home: bool
    pos: str
    features: dict[str, float]
    actual: dict[str, float | None]


class State:
    def __init__(self) -> None:
        self.skaters: dict[int, SkaterState] = defaultdict(SkaterState)
        self.goalies: dict[int, GoalieState] = defaultdict(GoalieState)
        self.teams: dict[int, TeamState] = defaultdict(TeamState)
        self.league = League()
        self.last_date: str | None = None

    # ---- league-level references ---------------------------------------------------------

    def pos_basis_h(self, pos: str, basis: str) -> float:
        lg = self.league.pos[pos]
        if basis == "pp":
            return lg.ptoi / lg.pgames if lg.pgames else 0.0
        return lg.toi / lg.games if lg.games else POS_TOI_FALLBACK_H[pos]

    def prior_rate(self, pos: str, stat: str) -> float:
        lg = self.league.pos[pos]
        return lg.x[stat] / lg.b[stat] if lg.b[stat] else 0.0

    def prior_finish(self, pos: str) -> float:
        lg = self.league.pos[pos]
        return lg.x["goals"] / lg.x["shots"] if lg.x["shots"] else 0.0

    def league_sv(self) -> float:
        return self.league.saves / self.league.sa if self.league.sa else 0.0

    def team_avg(self, stat: str) -> float:
        lg = self.league
        return lg.team_tot[stat] / lg.team_n[stat] if lg.team_n[stat] else 0.0

    def team_factor(self, team: int, stat: str, side: str) -> float:
        """Shrunk ratio of a team's per-game total ('for' or 'against') to the league average."""
        avg = self.team_avg(stat)
        if avg <= 0:
            return 1.0
        t = self.teams.get(team)
        if t is None:
            return 1.0
        s, n = (t.for_[stat], t.for_n[stat]) if side == "for" else (t.against[stat], t.against_n[stat])
        return ((s + TEAM_C * avg) / (n + TEAM_C)) / avg

    def home_factor(self, stat: str, home: bool) -> float:
        xh, bh, xa, ba = self.league.ha[stat]
        if not (bh and ba):
            return 1.0
        overall = (xh + xa) / (bh + ba)
        if overall <= 0:
            return 1.0
        return (xh / bh if home else xa / ba) / overall

    def playoff_factor(self, stat: str, playoff: bool) -> float:
        """Playoff games vs regular season, league-wide, shrunk toward no effect. 1.0 outside the playoffs."""
        if not playoff:
            return 1.0
        x_po, b_po, x_reg, b_reg = self.league.po[stat]
        if not b_reg or not x_reg:
            return 1.0
        reg = x_reg / b_reg
        return ((x_po + PLAYOFF_K_H * reg) / (b_po + PLAYOFF_K_H)) / reg

    def playoff_shots_factor(self, playoff: bool) -> float:
        if not playoff:
            return 1.0
        po_sum, po_n, reg_sum, reg_n = self.league.po_shots
        if not reg_n or not reg_sum:
            return 1.0
        reg = reg_sum / reg_n
        return ((po_sum + PLAYOFF_K_GAMES * reg) / (po_n + PLAYOFF_K_GAMES)) / reg

    def venue_factor(self, arena_team: int | None, stat: str) -> float:
        """Arena scorekeeping: totals in this team's home games vs. its road games, both shrunk."""
        if arena_team is None or stat not in VENUE_STATS:
            return 1.0
        game_avg = 2 * self.team_avg(stat)
        t = self.teams.get(arena_team)
        if t is None or game_avg <= 0:
            return 1.0
        h = (t.home_tot[stat] + VENUE_C * game_avg) / (t.nh + VENUE_C)
        r = (t.road_tot[stat] + VENUE_C * game_avg) / (t.nr + VENUE_C)
        return h / r

    def goalie_sv(self, goalie: int, k: float) -> float:
        lsv = self.league_sv()
        g = self.goalies.get(goalie)
        if g is None:
            return lsv
        return (g.saves + k * lsv) / (g.sa + k)

    def goalie_factor(self, mix: list[tuple[int, float]], k: float) -> float:
        """Expected (1 - sv) of the opposing goalie(s), relative to league: >1 = easier to score on."""
        lsv = self.league_sv()
        if not mix or lsv <= 0 or lsv >= 1:
            return 1.0
        total = sum(p for _, p in mix)
        return sum(p * (1 - self.goalie_sv(g, k)) for g, p in mix) / total / (1 - lsv)

    # ---- features ------------------------------------------------------------------------

    def _season_mean(self, seasons: dict[int, dict[str, list[float]]], season: int, stat: str) -> float | None:
        for s in (season, season - 10001):
            v = seasons.get(s, {}).get(stat)
            if v and v[1] > 0:
                return v[0] / v[1]
        return None

    def skater_features(
        self,
        player: int,
        pos: str,
        team: int,
        opp: int,
        home: bool,
        arena_team: int | None,
        opp_goalies: list[tuple[int, float]],
        season: int,
        playoff: bool = False,
    ) -> dict[str, float]:
        st = self.skaters.get(player) or SkaterState()
        f: dict[str, float] = {"games": float(st.games)}
        toi_avg = self.pos_basis_h(pos, "toi")
        pp_avg = self.pos_basis_h(pos, "pp")
        f["pos_toi"] = toi_avg
        f["pos_pp"] = pp_avg
        for i in range(H):
            f[f"eb.toi.{i}"] = (st.toi[i] + TOI_K * toi_avg) / (st.n[i] + TOI_K)
            f[f"eb.pp.{i}"] = (st.ptoi[i] + TOI_K * pp_avg) / (st.pn[i] + TOI_K)
            f[f"n.{i}"] = st.n[i]
        for s in SKATER_STATS:
            for i in range(H):
                f[f"x.{s}.{i}"] = st.x[s][i]
                f[f"b.{s}.{i}"] = st.b[s][i]
            prior = self.prior_rate(pos, s)
            f[f"prior.{s}"] = prior
            f[f"home.{s}"] = self.home_factor(s, home)
            f[f"venue.{s}"] = self.venue_factor(arena_team, s)
            f[f"po.{s}"] = self.playoff_factor(s, playoff)
            f[f"opp.{s}"] = self.team_factor(opp, s, "against")
            # Baselines (what a simple method would say): season average and last-10 average.
            fallback = prior * (pp_avg if basis_of(s) == "pp" else toi_avg)
            sm = self._season_mean(st.season, season, s)
            f[f"base_season.{s}"] = sm if sm is not None else fallback
            rec = st.recent[s]
            f[f"base_l10.{s}"] = sum(rec) / len(rec) if rec else fallback
            f[f"fallback.{s}"] = fallback
        f["prior_finish"] = self.prior_finish(pos)
        for j, k in enumerate(K_SV):
            f[f"gf.{j}"] = self.goalie_factor(opp_goalies, k)
        return f

    def goalie_features(self, goalie: int, team: int, opp: int, season: int, playoff: bool = False) -> dict[str, float]:
        g = self.goalies.get(goalie) or GoalieState()
        lg = self.league
        f: dict[str, float] = {"starts": float(g.starts), "sa_seen": g.sa}
        f["league_sv"] = self.league_sv()
        f["mu_league"] = lg.starter_sa / lg.starts if lg.starts else 0.0
        f["fd"] = self.team_factor(team, "shots", "against")  # own team's shots allowed
        f["fo"] = self.team_factor(opp, "shots", "for")  # opponent's shot volume
        f["po"] = self.playoff_shots_factor(playoff)
        for j, k in enumerate(K_SV):
            f[f"sv.{j}"] = self.goalie_sv(goalie, k)
        sv_l = f["league_sv"]
        fallback = {"saves": f["mu_league"] * sv_l, "goals_against": f["mu_league"] * (1 - sv_l)}
        for s in GOALIE_STATS:
            sm = self._season_mean(g.season, season, s)
            f[f"base_season.{s}"] = sm if sm is not None else fallback[s]
            rec = g.recent[s]
            f[f"base_l10.{s}"] = sum(rec) / len(rec) if rec else fallback[s]
            f[f"fallback.{s}"] = fallback[s]
        return f

    # ---- game level (team scoring, OT/SO, shutouts) ----------------------------------------

    def goals_per_shot(self) -> float:
        lg = self.league
        return lg.team_tot["goals"] / lg.team_tot["shots"] if lg.team_tot["shots"] else 0.0

    def team_features(
        self, team: int, opp: int, home: bool, opp_goalies: list[tuple[int, float]], season: int, p: str
    ) -> dict[str, float]:
        """Expected-goal ingredients for `team` against `opp` (prefix p = 'h' or 'a')."""
        f: dict[str, float] = {}
        f[f"{p}.ff"] = self.team_factor(team, "shots", "for")
        f[f"{p}.fa"] = self.team_factor(opp, "shots", "against")
        f[f"{p}.S"] = self.team_avg("shots") * f[f"{p}.ff"] * f[f"{p}.fa"]
        g_l = self.goals_per_shot()
        t = self.teams.get(team)
        for j, c in enumerate(FIN_C):
            if t is None or g_l <= 0:
                f[f"{p}.fin.{j}"] = 1.0
            else:
                f[f"{p}.fin.{j}"] = ((t.for_["goals"] + c * g_l) / (t.for_["shots"] + c)) / g_l
        for j, k in enumerate(K_SV):
            f[f"{p}.gf.{j}"] = self.goalie_factor(opp_goalies, k)
        f[f"{p}.home"] = self.home_factor("goals", home)
        rec = t.record.get(season) if t else None
        prev = t.record.get(season - 10001) if t else None
        league_goals = self.team_avg("goals")
        if rec and rec[0]:
            f[f"{p}.base_season"] = rec[2] / rec[0]
        elif prev and prev[0]:
            f[f"{p}.base_season"] = prev[2] / prev[0]
        else:
            f[f"{p}.base_season"] = league_goals
        f[f"{p}.base_l10"] = sum(t.recent_goals) / len(t.recent_goals) if t and t.recent_goals else league_goals
        f[f"{p}.wpct"] = rec[1] / rec[0] if rec and rec[0] else (prev[1] / prev[0] if prev and prev[0] else 0.5)
        return f

    def goalie_so_rate(self, goalie: int | None, season: int) -> float:
        lg_rate = self.league.shutouts / self.league.so_known if self.league.so_known else 0.0
        g = self.goalies.get(goalie) if goalie is not None else None
        if g is None:
            return lg_rate
        m = self._season_mean(g.season, season, "shutout")
        return m if m is not None else lg_rate

    def game_features(
        self,
        home: int,
        away: int,
        mixes: dict[int, list[tuple[int, float]]],
        season: int,
    ) -> dict[str, float]:
        lg = self.league
        f = self.team_features(home, away, True, mixes.get(away, []), season, "h")
        f |= self.team_features(away, home, False, mixes.get(home, []), season, "a")
        f["gL"] = self.goals_per_shot()
        f["ot_q"] = (lg.ended_ot + EVEN_PRIOR * 0.5) / (lg.tied_reg + EVEN_PRIOR)
        so_games = lg.tied_reg - lg.ended_ot
        f["so_home"] = (lg.so_home_wins + EVEN_PRIOR * 0.5) / (so_games + EVEN_PRIOR)
        f["lg_home_win"] = (lg.home_wins + EVEN_PRIOR * 0.5) / (lg.games + EVEN_PRIOR)
        f["lg_so"] = lg.shutouts / lg.so_known if lg.so_known else 0.0
        for side, team in (("h", home), ("a", away)):
            mix = mixes.get(team, [])
            starter = max(mix, key=lambda t: t[1])[0] if mix else None
            f[f"{side}.g_so"] = self.goalie_so_rate(starter, season)
        return f

    # ---- learning ------------------------------------------------------------------------

    def _learn_skater(self, line: SkaterLine, season: int, date: str, playoff: bool = False) -> None:
        st = self.skaters[line.player]
        st.games += 1
        st.last_date = date
        st.last_team = line.team
        for vec in (st.n, st.toi, st.pn, st.ptoi, *st.x.values(), *st.b.values()):
            _decayed(vec)
        lg = self.league.pos[line.pos]
        toi_h = line.toi_s / 3600 if line.toi_s is not None else None
        pp_h = line.pp_toi_s / 3600 if line.pp_toi_s is not None else None
        if toi_h is not None:
            lg.games += 1
            lg.toi += toi_h
            for i in range(H):
                st.n[i] += 1
                st.toi[i] += toi_h
        if pp_h is not None:
            lg.pgames += 1
            lg.ptoi += pp_h
            for i in range(H):
                st.pn[i] += 1
                st.ptoi[i] += pp_h
        seas = st.season.setdefault(season, {})
        for s in SKATER_STATS:
            v = line.stats[s]
            if v is None:
                continue
            seas.setdefault(s, [0.0, 0.0])
            seas[s][0] += v
            seas[s][1] += 1
            st.recent[s].append(v)
            basis = pp_h if basis_of(s) == "pp" else toi_h
            if basis is None:
                continue
            for i in range(H):
                st.x[s][i] += v
                st.b[s][i] += basis
            lg.x[s] += v
            lg.b[s] += basis
            ha = self.league.ha[s]
            off = 0 if line.home else 2
            ha[off] += v
            ha[off + 1] += basis
            po = self.league.po[s]
            off = 0 if playoff else 2
            po[off] += v
            po[off + 1] += basis

    def _learn_goalie(self, line: GoalieLine, season: int) -> None:
        if line.sa is None or line.saves is None:
            return
        g = self.goalies[line.player]
        g.saves = g.saves * GOALIE_DECAY + line.saves
        g.sa = g.sa * GOALIE_DECAY + line.sa
        lg = self.league
        lg.saves += line.saves
        lg.sa += line.sa
        g.last_team = line.team
        if line.started:
            g.starts += 1
            lg.starter_sa += line.sa
            lg.starts += 1
            seas = g.season.setdefault(season, {})
            if line.shutout is not None:
                lg.so_known += 1
                lg.shutouts += line.shutout
            so = None if line.shutout is None else float(line.shutout)
            for s, v in (("saves", line.saves), ("goals_against", line.ga), ("shutout", so)):
                if v is None:
                    continue
                seas.setdefault(s, [0.0, 0.0])
                seas[s][0] += v
                seas[s][1] += 1
                if s in g.recent:
                    g.recent[s].append(v)

    def _learn_result(self, game: GameRecord) -> None:
        won = game.home_won
        if won is None or game.home_goals is None or game.away_goals is None:
            return
        lg = self.league
        lg.games += 1
        lg.home_wins += won
        if not game.playoff and game.ended_in in ("OT", "SO"):
            lg.tied_reg += 1
            if game.ended_in == "OT":
                lg.ended_ot += 1
            elif game.home_so_win:
                lg.so_home_wins += 1
        for team, goals, w in ((game.home_team, game.home_goals, won), (game.away_team, game.away_goals, not won)):
            t = self.teams[team]
            rec = t.record.setdefault(game.season, [0.0, 0.0, 0.0])
            rec[0] += 1
            rec[1] += w
            rec[2] += goals
            t.recent_goals.append(goals)

    def _learn_teams(self, game: GameRecord) -> None:
        totals: dict[int, dict[str, float | None]] = {}
        for team in (game.home_team, game.away_team):
            lines = [s for s in game.skaters if s.team == team]
            tot: dict[str, float | None] = {}
            for s in SKATER_STATS:
                vals = [ln.stats[s] for ln in lines]
                tot[s] = None if not lines or any(v is None for v in vals) else sum(v for v in vals if v is not None)
            totals[team] = tot
        lg = self.league
        for team, opp in ((game.home_team, game.away_team), (game.away_team, game.home_team)):
            t = self.teams[team]
            for s in SKATER_STATS:
                for d in (t.for_, t.for_n, t.against, t.against_n):
                    d[s] *= TEAM_DECAY
                mine, theirs = totals[team][s], totals[opp][s]
                if mine is not None:
                    t.for_[s] += mine
                    t.for_n[s] += 1
                    lg.team_tot[s] += mine
                    lg.team_n[s] += 1
                    if s == "shots":
                        off = 0 if game.playoff else 2
                        lg.po_shots[off] += mine
                        lg.po_shots[off + 1] += 1
                if theirs is not None:
                    t.against[s] += theirs
                    t.against_n[s] += 1
        if game.neutral:
            return
        home, road = self.teams[game.home_team], self.teams[game.away_team]
        home.nh = home.nh * VENUE_DECAY + 1
        road.nr = road.nr * VENUE_DECAY + 1
        for s in VENUE_STATS:
            a, b = totals[game.home_team][s], totals[game.away_team][s]
            home.home_tot[s] *= VENUE_DECAY
            road.road_tot[s] *= VENUE_DECAY
            if a is not None and b is not None:
                home.home_tot[s] += a + b
                road.road_tot[s] += a + b

    def learn(self, game: GameRecord) -> None:
        for line in game.skaters:
            self._learn_skater(line, game.season, game.date, game.playoff)
        for gl in game.goalies:
            self._learn_goalie(gl, game.season)
        self._learn_teams(game)
        self._learn_result(game)
        self.last_date = game.date


def starters(game: GameRecord) -> dict[int, int]:
    """team -> starting goalie (the backtest knows who started; see docs/05 'lineup oracle')."""
    return {g.team: g.player for g in game.goalies if g.started}


def walk(games: list[GameRecord], state: State | None = None, *, emit: bool = True) -> Iterator[Row]:
    """Replay games in order, yielding one Row per skater appearance and goalie start,
    each built only from games on earlier dates. emit=False only learns (live projections)."""
    state = state or State()
    for date, day in by_date(games):
        for game in day if emit else ():
            start = starters(game)
            arena = None if game.neutral else game.home_team
            for line in game.skaters:
                opp_g = start.get(line.opp)
                feats = state.skater_features(
                    line.player,
                    line.pos,
                    line.team,
                    line.opp,
                    line.home,
                    arena,
                    [(opp_g, 1.0)] if opp_g is not None else [],
                    game.season,
                    game.playoff,
                )
                feats |= {
                    "meta.game": float(game.game_id),
                    "meta.team": float(line.team),
                    "meta.home": float(line.home),
                }
                actual: dict[str, float | None] = dict(line.stats)
                actual["first_goal"] = float(line.player == game.first_goal_player) if game.has_pbp else None
                yield Row(
                    "skater",
                    game.game_id,
                    date,
                    game.season,
                    line.player,
                    line.team,
                    line.opp,
                    line.home,
                    line.pos,
                    feats,
                    actual,
                )
            for gl in game.goalies:
                if not gl.started or gl.sa is None:
                    continue
                feats = state.goalie_features(gl.player, gl.team, gl.opp, game.season, game.playoff)
                yield Row(
                    "goalie",
                    game.game_id,
                    date,
                    game.season,
                    gl.player,
                    gl.team,
                    gl.opp,
                    gl.home,
                    "G",
                    feats,
                    {"saves": gl.saves, "goals_against": gl.ga, "sa": gl.sa},
                )
            if game.home_goals is not None and game.away_goals is not None:
                mixes = {t: [(g, 1.0)] for t, g in start.items()}
                feats = state.game_features(game.home_team, game.away_team, mixes, game.season)
                feats |= {
                    "meta.game": float(game.game_id),
                    "meta.home_team": float(game.home_team),
                    "meta.away_team": float(game.away_team),
                    "playoff": float(game.playoff),
                }
                so = {g.team: g.shutout for g in game.goalies if g.started}
                # Starting goalie's saves and whether he got the win (for Saves + Win).
                sw: dict[int, tuple[float | None, float | None]] = {}
                for gl in game.goalies:
                    if gl.started and gl.saves is not None and gl.decision is not None:
                        sw[gl.team] = (float(gl.saves), float(gl.decision == "W"))
                h_sv, h_w = sw.get(game.home_team, (None, None))
                a_sv, a_w = sw.get(game.away_team, (None, None))
                yield Row(
                    "game",
                    game.game_id,
                    date,
                    game.season,
                    0,
                    game.home_team,
                    game.away_team,
                    True,
                    "",
                    feats,
                    {
                        "home_goals": float(game.home_goals),
                        "away_goals": float(game.away_goals),
                        "home_win": float(bool(game.home_won)),
                        "h_so": None if so.get(game.home_team) is None else float(bool(so[game.home_team])),
                        "a_so": None if so.get(game.away_team) is None else float(bool(so[game.away_team])),
                        "h_saves": h_sv,
                        "h_gw": h_w,
                        "a_saves": a_sv,
                        "a_gw": a_w,
                    },
                )
        for game in day:
            state.learn(game)
