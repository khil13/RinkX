"""Champion / challenger: a new model version is published only after it beats the current one
on live results.

Each model family (shots, scoring, goalie, game model ...) has one champion version, the one the
site publishes. A newer version that passes its walk-forward test becomes the challenger. Every
time a line is priced, the challenger's probability for that same line is frozen beside the
champion's (shadow_predictions). The challenger is never shown or used for leans.

Once props are graded, the two are compared on exactly the same props by log score (pushes and
voids excluded; several books or re-pricings of one prop count once, averaged). Decision, per
family, once at least MIN_PROPS props are compared:

    promote   the challenger's 95% interval for (its log score - the champion's) is above 0
    reject    the interval is below 0 (it stays retired; the next version gets a fresh start)
    wait      otherwise; there is no deadline

A promotion takes effect on the next run: the new champion's projections are published from then
on, and the family's live calibrators are reset (they were fit to the old champion).
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

from rinkx.ingestion.runs import ingestion_run, register_source
from rinkx.models.fit import FAMILY, GAME_MARKETS, MARKETS
from rinkx.pricing.model_probs import AtLine, at_line, yes_no
from rinkx.timeutil import iso

MIN_PROPS = 250  # graded props compared before any decision
Z = 1.96
EPS = 1e-6
OVER = ("over", "yes", "home")


def model_at(market: str, kind: str, pmf: list[float], line: float | None) -> AtLine | None:
    """A projection's probabilities at a line, the way pricing reads them (None if it can't)."""
    if market == "goalie_saves_and_win":
        if line is None:
            return None
        p = sum(pmf[math.floor(line) + 1 :])  # pmf: P(k saves AND a win)
        return AtLine(p, 1 - p, 0.0)
    if market == "game_moneyline":
        return AtLine(pmf[1], 1 - pmf[1], 0.0)
    if kind == "over_under":
        return None if line is None else at_line(pmf, line)
    return yes_no(pmf)


def record_shadow(
    conn: sqlite3.Connection,
    prediction_id: int,
    *,
    game_id: int,
    player_id: int | None,
    side: str,
    market_id: int,
    market: str,
    kind: str,
    line: float | None,
    champion_version_id: int,
    p_champion: float,
    now: datetime,
) -> int:
    """Freeze each challenger's probability for the line just priced. Returns rows written."""
    rows = conn.execute(
        "SELECT c.model_version_id, c.pmf FROM challenger_projections c JOIN model_versions v "
        "ON v.id = c.model_version_id WHERE c.game_id = ? AND coalesce(c.player_id, 0) = ? AND c.side = ? "
        "AND c.market_id = ? AND v.status = 'challenger'",
        (game_id, player_id or 0, side, market_id),
    ).fetchall()
    n = 0
    for mv, pmf_text in rows:
        at = model_at(market, kind, json.loads(pmf_text)["p"], line)
        if at is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO shadow_predictions (prediction_id, model_version_id, champion_version_id, "
            "p_champion, p_challenger, created_at) VALUES (?,?,?,?,?,?)",
            (prediction_id, mv, champion_version_id, round(p_champion, 6), round(at.over_given_no_push, 6), iso(now)),
        )
        n += 1
    return n


def _ll(p: float, y: int) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p if y else 1 - p)


COMPARE_SQL = """
SELECT s.model_version_id, s.champion_version_id, s.p_champion, s.p_challenger, r.result,
       p.game_id, coalesce(p.player_id, 0) AS player_id, p.market_id, p.line,
       ch.model_family, ch.version AS challenger, cv.version AS champion
FROM shadow_predictions s
JOIN predictions p ON p.id = s.prediction_id
JOIN model_results r ON r.prediction_id = p.id
JOIN model_versions ch ON ch.id = s.model_version_id
JOIN model_versions cv ON cv.id = s.champion_version_id
WHERE r.result NOT IN ('push','void')
"""


def compare(conn: sqlite3.Connection) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(family, champion, challenger) -> paired log-score comparison on graded props."""
    props: dict[tuple[str, str, str], dict[tuple[int, int, int, float | None], list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in conn.execute(COMPARE_SQL):
        y = 1 if r["result"] in OVER else 0
        d = _ll(r["p_challenger"], y) - _ll(r["p_champion"], y)
        key = (r["model_family"], r["champion"], r["challenger"])
        props[key][(r["game_id"], r["player_id"], r["market_id"], r["line"])].append(d)
    out = {}
    for key, by_prop in props.items():
        diffs = [sum(v) / len(v) for v in by_prop.values()]
        n = len(diffs)
        mean = sum(diffs) / n
        se = math.sqrt(sum((x - mean) ** 2 for x in diffs) / (n - 1) / n) if n > 1 else math.inf
        out[key] = {"n": n, "mean_diff": mean, "se": se, "lo": mean - Z * se, "hi": mean + Z * se}
    return out


def decide(c: dict[str, Any]) -> str:
    if c["n"] < MIN_PROPS:
        return "wait"
    if c["lo"] > 0:
        return "promoted"
    if c["hi"] < 0:
        return "rejected"
    return "wait"


def run_promotion(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """Promote or reject challengers whose live evidence is decisive. Returns the decisions made."""
    from rinkx.models.project import MODELS_SOURCE  # project imports pricing; avoid a cycle

    src = register_source(conn, MODELS_SOURCE)
    decisions = []
    with ingestion_run(conn, src, "promotion") as run:
        for (family, champ, chal), c in compare(conn).items():
            status = conn.execute(
                "SELECT status FROM model_versions WHERE model_family = ? AND version = ?", (family, chal)
            ).fetchone()
            if status is None or status[0] != "challenger":
                continue
            d = decide(c)
            if d == "wait":
                continue
            if d == "promoted":
                conn.execute(
                    "UPDATE model_versions SET status = 'retired' WHERE model_family = ? AND status = 'champion'",
                    (family,),
                )
                conn.execute(
                    "UPDATE model_versions SET status = 'champion', promoted_at = ? WHERE model_family = ? "
                    "AND version = ?",
                    (iso(now), family, chal),
                )
                # live calibrators were fit to the old champion's probabilities
                codes = [m for m, st in (MARKETS | GAME_MARKETS).items() if FAMILY[st] == family]
                conn.execute(
                    "UPDATE calibrators SET is_current = 0 WHERE is_current = 1 AND market_id IN "
                    f"(SELECT id FROM markets WHERE code IN ({','.join('?' * len(codes))}))",
                    codes,
                )
            else:
                conn.execute(
                    "UPDATE model_versions SET status = 'retired' WHERE model_family = ? AND version = ?",
                    (family, chal),
                )
            conn.execute(
                "INSERT INTO model_promotions (model_family, champion, challenger, decision, n_props, mean_diff, se, "
                "decided_at) VALUES (?,?,?,?,?,?,?,?)",
                (family, champ, chal, d, c["n"], c["mean_diff"], c["se"], iso(now)),
            )
            decisions.append({"family": family, "champion": champ, "challenger": chal, "decision": d, **c})
        run.rows_upserted = len(decisions)
        run.meta["decisions"] = [f"{x['family']}: {x['challenger']} {x['decision']}" for x in decisions]
    return decisions


def published(conn: sqlite3.Connection) -> dict[str, Any]:
    """Per family: champion, challenger, the live comparison so far, and past decisions."""
    from rinkx.models.project import challengers, champions

    champs, chals = champions(conn), challengers(conn)
    comp = compare(conn)
    families = []
    for family in sorted(set(champs) | set(chals)):
        champ, chal = champs.get(family), chals.get(family)
        c = comp.get((family, champ or "", chal or "")) if champ and chal else None
        families.append(
            {
                "family": family,
                "champion": champ,
                "challenger": chal,
                "live": (
                    {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in c.items()}
                    | {"status": decide(c)}
                    if c
                    else None
                ),
            }
        )
    history = [
        dict(r)
        for r in conn.execute(
            "SELECT model_family AS family, champion, challenger, decision, n_props, mean_diff, se, decided_at "
            "FROM model_promotions ORDER BY decided_at DESC, id DESC"
        )
    ]
    return {"min_props": MIN_PROPS, "families": families, "history": history}
