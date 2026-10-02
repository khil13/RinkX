"""Odds jobs: match events, then fetch game lines and props within the credit budget.

Runs only when ODDS_API_KEY is set. Each step is an ingestion run, so failures show up as a
failed/partial "Sportsbook lines" feed and on the Admin page. Unmatched players and market
keys are recorded, never guessed.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rinkx.config import REPO_ROOT
from rinkx.ingestion.odds import budget as bud
from rinkx.ingestion.odds.client import BASE, OddsClient, OddsError, Usage, credits_for
from rinkx.ingestion.odds.parse import OddsParseError, parse_event, parse_quotes
from rinkx.ingestion.odds.resolve import PlayerResolver, load_config_aliases, match_game, resolve_open_issues
from rinkx.ingestion.odds.store import assemble, mark_removed, upsert_line
from rinkx.ingestion.runs import SourceSpec, ingestion_run, register_source
from rinkx.timeutil import EASTERN, iso, parse_iso

log = logging.getLogger("rinkx.odds")

SOURCE = SourceSpec(
    "the_odds_api",
    "The Odds API: sportsbook lines",
    "odds",
    "paid",
    BASE,
    "The Odds API, personal plan. Lines shown only to the owner (encrypted site); not redistributed.",
    0.9,
)
BOOKS_FILE = REPO_ROOT / "config/books.yml"
MARKETS_FILE = REPO_ROOT / "config/odds_markets.yml"


@dataclass
class OddsConfig:
    books: list[dict[str, str]]
    sport: str
    regions: str
    market_map: dict[str, str]  # vendor key -> markets.code
    budget: bud.BudgetConfig
    aliases: dict[str, int] = field(default_factory=dict)

    @property
    def book_keys(self) -> list[str]:
        return [b["code"] for b in self.books]

    @staticmethod
    def load(books: Path = BOOKS_FILE, markets: Path = MARKETS_FILE, budget: Path = bud.BUDGET_FILE) -> OddsConfig:
        b: dict[str, Any] = yaml.safe_load(books.read_text())
        m: dict[str, Any] = yaml.safe_load(markets.read_text())
        return OddsConfig(
            list(b["books"]),
            m["sport"],
            m["regions"],
            dict(m["markets"]),
            bud.BudgetConfig.load(budget),
            load_config_aliases(),
        )


def sync_books(conn: sqlite3.Connection, cfg: OddsConfig) -> dict[str, int]:
    conn.execute("UPDATE sportsbooks SET is_enabled = 0")
    for b in cfg.books:
        conn.execute(
            "INSERT INTO sportsbooks (code, name, regions, is_enabled) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(code) DO UPDATE SET name = excluded.name, is_enabled = 1",
            (b["code"], b["name"], json.dumps([cfg.regions])),
        )
    return {r[0]: r[1] for r in conn.execute("SELECT code, id FROM sportsbooks WHERE is_enabled = 1")}


def _record_usage(
    conn: sqlite3.Connection,
    now: datetime,
    endpoint: str,
    usage: Usage,
    planned: int,
    game_id: int | None = None,
    markets: list[str] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO odds_usage (at, endpoint, game_id, markets, credits_charged, credits_remaining, credits_used) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            iso(now),
            endpoint,
            game_id,
            json.dumps(markets or []),
            usage.charged if usage.charged is not None else planned,
            usage.remaining,
            usage.used,
        ),
    )


def remaining_credits(conn: sqlite3.Connection, cfg: bud.BudgetConfig, now: datetime) -> int:
    """The API's own count when we have one; otherwise the plan minus what we spent this month."""
    row = conn.execute(
        "SELECT credits_remaining FROM odds_usage WHERE credits_remaining IS NOT NULL ORDER BY at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row:
        return int(row[0])
    local = now.astimezone(EASTERN)
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = conn.execute(
        "SELECT ifnull(sum(credits_charged), 0) FROM odds_usage WHERE at >= ?", (iso(month_start),)
    ).fetchone()[0]
    return cfg.monthly_credits - int(spent)


def _today_start(now: datetime) -> datetime:
    return now.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)


def _store_payload(
    conn: sqlite3.Connection,
    cfg: OddsConfig,
    payload: dict[str, Any],
    game_id: int,
    book_ids: dict[str, int],
    resolver: PlayerResolver,
    source_id: int,
    now: datetime,
    unmapped: set[str],
    covered: list[str],
) -> int:
    """Store one event payload's lines. `covered` = vendor markets this call asked for."""
    _, quotes = parse_quotes(payload)
    g = conn.execute("SELECT home_team_id, away_team_id FROM games WHERE id = ?", (game_id,)).fetchone()
    teams = (int(g[0]), int(g[1]))
    at = iso(now)
    seen: set[int] = set()
    written = 0
    for ln in assemble(quotes):
        code = cfg.market_map.get(ln.market)
        if code is None:
            unmapped.add(ln.market)
            continue
        if ln.book not in book_ids:
            continue
        market_id = conn.execute("SELECT id FROM markets WHERE code = ?", (code,)).fetchone()[0]
        player_id = team_id = None
        if ln.subject is not None:
            player_id = resolver.resolve(ln.subject, teams, game_id)
            if player_id is None:
                continue  # unresolved: queued for review, never shown
        elif ln.market == "h2h":
            team_id = teams[0]
        pid, state = upsert_line(
            conn,
            game_id=game_id,
            market_id=market_id,
            book_id=book_ids[ln.book],
            player_id=player_id,
            team_id=team_id,
            line=ln.line,
            over=ln.over,
            under=ln.under,
            at=at,
            source_id=source_id,
            source_ref=f"{payload.get('id')}/{ln.book}/{ln.market}@{ln.updated or at}",
        )
        seen.add(pid)
        written += state != "same"
    market_ids = [
        conn.execute("SELECT id FROM markets WHERE code = ?", (cfg.market_map[m],)).fetchone()[0]
        for m in covered
        if m in cfg.market_map
    ]
    written += mark_removed(conn, game_id, market_ids, list(book_ids.values()), seen, at, source_id)
    return written


def run_odds(conn: sqlite3.Connection, client: OddsClient, now: datetime, cfg: OddsConfig | None = None) -> None:
    cfg = cfg or OddsConfig.load()
    src = register_source(conn, SOURCE)
    book_ids = sync_books(conn, cfg)
    conn.commit()
    resolver = PlayerResolver(conn, src, now, cfg.aliases)

    with ingestion_run(conn, src, "odds.events") as run:
        events, usage = client.events()
        _record_usage(conn, now, "events", usage, 0)
        run.rows_read = len(events)
        for raw in events:
            try:
                ev = parse_event(raw)
            except OddsParseError as exc:
                run.errors.append(str(exc))
                continue
            known = conn.execute(
                "SELECT game_id FROM odds_events WHERE source_id = ? AND event_id = ?", (src, ev.id)
            ).fetchone()
            if known:
                continue
            gid = match_game(conn, ev.home_team, ev.away_team, ev.commence_time)
            if gid is None:
                if parse_iso(ev.commence_time) - now < timedelta(days=3):
                    run.errors.append(f"no game for {ev.away_team} at {ev.home_team} {ev.commence_time}")
                continue
            conn.execute(
                "INSERT INTO odds_events (source_id, event_id, game_id, commence_time, first_seen_at) "
                "VALUES (?,?,?,?,?)",
                (src, ev.id, gid, ev.commence_time, iso(now)),
            )
            run.rows_upserted += 1
        run.quota_remaining = usage.remaining

    today = _today_start(now)
    spent_today = int(
        conn.execute("SELECT ifnull(sum(credits_charged), 0) FROM odds_usage WHERE at >= ?", (iso(today),)).fetchone()[
            0
        ]
    )
    gl_rows = conn.execute(
        "SELECT at FROM odds_usage WHERE endpoint = 'game_odds' AND at >= ? ORDER BY at DESC", (iso(today),)
    ).fetchall()
    last_gl = conn.execute("SELECT max(at) FROM odds_usage WHERE endpoint = 'game_odds'").fetchone()[0]
    cands = [
        bud.Candidate(r[0], r[1], parse_iso(r[2]), parse_iso(r[3]) if r[3] else None)
        for r in conn.execute(
            "SELECT e.game_id, e.event_id, g.start_time_utc, e.props_fetched_at FROM odds_events e "
            "JOIN games g ON g.id = e.game_id WHERE e.source_id = ? AND g.status IN ('scheduled','pregame')",
            (src,),
        )
    ]
    remaining = remaining_credits(conn, cfg.budget, now)
    plan = bud.plan(
        cfg.budget,
        now,
        remaining=remaining,
        spent_today=spent_today,
        game_lines_today=len(gl_rows),
        last_game_lines=parse_iso(last_gl) if last_gl else None,
        candidates=cands,
        books=cfg.book_keys,
    )
    unmapped: set[str] = set()

    if plan.game_lines:
        with ingestion_run(conn, src, "odds.game_lines") as run:
            data, usage = client.game_odds(cfg.budget.game_markets, cfg.book_keys)
            _record_usage(
                conn,
                now,
                "game_odds",
                usage,
                credits_for(cfg.budget.game_markets, cfg.book_keys),
                markets=cfg.budget.game_markets,
            )
            run.quota_remaining = usage.remaining
            for payload in data:
                ev_id = str(payload.get("id"))
                row = conn.execute(
                    "SELECT game_id FROM odds_events WHERE source_id = ? AND event_id = ?", (src, ev_id)
                ).fetchone()
                if row is None:
                    continue
                try:
                    run.rows_upserted += _store_payload(
                        conn, cfg, payload, int(row[0]), book_ids, resolver, src, now, unmapped, cfg.budget.game_markets
                    )
                except OddsParseError as exc:
                    run.errors.append(f"{ev_id}: {exc}")

    if plan.props:
        with ingestion_run(conn, src, "odds.props") as run:
            for cand, markets in plan.props:
                try:
                    payload, usage = client.event_odds(cand.event_id, markets, cfg.book_keys)
                except OddsError as exc:
                    run.errors.append(str(exc))
                    if exc.status in (401, 429):
                        break  # bad key or out of credits: stop spending
                    continue
                _record_usage(
                    conn,
                    now,
                    "event_odds",
                    usage,
                    credits_for(markets, cfg.book_keys),
                    game_id=cand.game_id,
                    markets=markets,
                )
                try:
                    run.rows_upserted += _store_payload(
                        conn, cfg, payload, cand.game_id, book_ids, resolver, src, now, unmapped, markets
                    )
                except OddsParseError as exc:
                    run.errors.append(f"{cand.event_id}: {exc}")
                    continue
                conn.execute(
                    "UPDATE odds_events SET props_fetched_at = ? WHERE source_id = ? AND event_id = ?",
                    (iso(now), src, cand.event_id),
                )
                run.http_calls += 1
            run.meta["games"] = len(plan.props)
            run.meta["unresolved_players"] = sorted(resolver.unresolved)
    resolve_open_issues(conn, src, now)
    if unmapped:
        log.warning("odds: %d market keys not mapped in config/odds_markets.yml", len(unmapped))
    conn.execute(
        "INSERT INTO ingestion_runs (source_id, job_name, status, started_at, finished_at, meta) "
        "VALUES (?, 'odds.plan', 'succeeded', ?, ?, ?)",
        (
            src,
            iso(now),
            iso(now),
            json.dumps(
                {
                    "allowance": plan.allowance,
                    "remaining": remaining,
                    "game_lines": plan.game_lines,
                    "prop_games": len(plan.props),
                    "unmapped_markets": sorted(unmapped),
                }
            ),
        ),
    )
    conn.commit()
