import pytest

from rinkx.analytics.hitrates import StatSpec, hit_rates, summarize, wilson_interval

SOG = StatSpec("sog", "Shots on goal", (1, 2, 3, 4, 5))


def game(i: int, sog: int | None, season: int = 20262027) -> dict:
    return {"date": f"2026-10-{30 - i:02d}", "season": season, "sog": sog}


def test_summarize_counts_each_threshold():
    s = summarize([6, 5, 3, 7, 5], (1, 2, 3, 4, 5, 6))
    assert s["counts"] == [5, 5, 5, 4, 4, 2]
    assert (s["games"], s["known"], s["missing"]) == (5, 5, 0)
    assert s["mean"] == 5.2 and s["median"] == 5 and s["sd"] == pytest.approx(1.48, abs=0.01)


def test_missing_values_are_excluded_not_zero():
    s = summarize([4, None, 2, None], (1, 3))
    assert (s["games"], s["known"], s["missing"]) == (4, 2, 2)
    assert s["counts"] == [2, 1]  # a None never counts as a miss (0) or a hit
    assert s["mean"] == 3.0


def test_empty_and_single_game():
    assert summarize([], (1,)) == {"games": 0, "known": 0, "missing": 0, "counts": [0], "mean": None,
                                   "median": None, "sd": None}  # fmt: skip
    assert summarize([3], (1,))["sd"] is None  # no spread from one game


def test_windows_use_most_recent_games_played_and_span_seasons():
    played = [game(i, sog) for i, sog in enumerate([6, 5, 3, 7, 5, 2, 0, 4])]
    played += [game(10 + i, 3, season=20252026) for i in range(15)]
    r = hit_rates(played, [SOG], season=20262027, last_season=20252026)["sog"]
    w = r["windows"]
    assert w["L5"]["counts"] == [5, 5, 5, 4, 4]  # 6,5,3,7,5
    assert w["L5"]["from"] == "2026-10-26" and w["L5"]["to"] == "2026-10-30"
    assert w["L10"]["games"] == 10  # 8 this season + 2 from last season: windows cross seasons
    assert w["L20"]["games"] == 20
    assert w["season"]["games"] == 8 and w["last_season"]["games"] == 15
    assert w["season"]["counts"][0] == 7  # one game with 0 shots


def test_fewer_games_than_window():
    r = hit_rates([game(0, 2), game(1, 4)], [SOG], season=20262027, last_season=None)["sog"]["windows"]
    assert r["L20"]["games"] == 2  # never padded
    assert r["last_season"]["games"] == 0


def test_wilson_interval():
    lo, hi = wilson_interval(4, 5)
    assert lo == pytest.approx(0.376, abs=0.001) and hi == pytest.approx(0.964, abs=0.001)
    assert wilson_interval(0, 0) is None
    assert wilson_interval(10, 10)[1] == 1.0
