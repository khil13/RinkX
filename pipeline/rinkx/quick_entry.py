"""Quick Entry: the owner's write path, via GitHub issue forms (docs/03-api.md).

Each pipeline run reads open issues titled "Quick Entry: ...", applies the ones opened by the
repository owner, and records each in `quick_entries` so it is applied exactly once. Issues
from anyone else are ignored and left untouched.

The issue is commented on and closed only after the run has saved the store. The comment
never contains projections or any other private data: the repository is public.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from rinkx.ingestion.runs import SourceSpec, ingestion_run, register_source
from rinkx.timeutil import iso

log = logging.getLogger("rinkx.quick_entry")

TITLE_PREFIX = "Quick Entry:"
SOURCE = SourceSpec(
    "quick_entry",
    "Quick Entry (owner's GitHub issues)",
    "manual",
    "free",
    "",
    "Entered by the site owner, each with a public source URL.",
    0.9,
)
OUT_REASONS = {"injury", "illness", "healthy scratch", "rest", "suspension", "personal", "other"}
NEWS_CATEGORIES = {"injury", "lineup", "goalie", "scratch", "suspension", "coach", "rest", "transaction", "general"}
RELIABILITY = {"official": "official", "beat reporter": "beat_reporter", "aggregator": "aggregator"}
URL_RE = re.compile(r"^https?://\S+$")


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    author: str
    created_at: str


class IssueTracker(Protocol):
    def open_issues(self) -> list[Issue]: ...
    def comment(self, number: int, body: str) -> None: ...
    def close(self, number: int, completed: bool) -> None: ...


class GhIssues:
    """GitHub issues through the `gh` CLI (authenticated by GH_TOKEN in Actions)."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def _gh(self, *args: str) -> str:
        proc = subprocess.run(["gh", *args, "--repo", self.repo], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"gh {args[0]} {args[1]} failed: {proc.stderr.strip()}")
        return proc.stdout

    def open_issues(self) -> list[Issue]:
        out = self._gh(
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            "100",
            "--search",
            f'"{TITLE_PREFIX}" in:title',
            "--json",
            "number,title,body,author,createdAt",
        )
        return [
            Issue(
                int(i["number"]),
                i["title"],
                i.get("body") or "",
                (i.get("author") or {}).get("login", ""),
                i["createdAt"],
            )
            for i in json.loads(out)
        ]

    def comment(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body", body)

    def close(self, number: int, completed: bool) -> None:
        self._gh("issue", "close", str(number), "--reason", "completed" if completed else "not planned")


# ---- parsing --------------------------------------------------------------------------------


def parse_form(body: str) -> dict[str, str]:
    """Issue-form bodies render as '### Label' headings followed by the answer."""
    fields: dict[str, str] = {}
    label: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("### "):
            if label is not None:
                fields[label] = "\n".join(buf).strip()
            label, buf = line[4:].strip().lower(), []
        elif label is not None:
            buf.append(line)
    if label is not None:
        fields[label] = "\n".join(buf).strip()
    return {k: ("" if v == "_No response_" else v) for k, v in fields.items()}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


class Rejected(ValueError):
    pass


def _field(form: dict[str, str], *names: str) -> str:
    for n in names:
        for k, v in form.items():
            if k.startswith(n):
                return v.strip()
    return ""


def _resolve_game(conn: sqlite3.Connection, text: str, team: int | None) -> sqlite3.Row:
    g: sqlite3.Row | None
    if text.isdigit():
        g = conn.execute("SELECT * FROM games WHERE nhl_game_id = ?", (int(text),)).fetchone()
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) and team is not None:
        g = conn.execute(
            "SELECT * FROM games WHERE game_date = ? AND ? IN (home_team_id, away_team_id)", (text, team)
        ).fetchone()
    else:
        raise Rejected("Game must be an NHL game id, or a date (YYYY-MM-DD) together with a team.")
    if g is None:
        raise Rejected(f"No game found for '{text}'.")
    if g["status"] not in ("scheduled", "pregame"):
        raise Rejected("That game has already started or finished; entries only apply to upcoming games.")
    return g


def _resolve_team(conn: sqlite3.Connection, text: str) -> int | None:
    if not text:
        return None
    row = conn.execute("SELECT id FROM teams WHERE abbrev = ?", (text.upper(),)).fetchone()
    if row is None:
        raise Rejected(f"Unknown team '{text}'. Use the 3-letter abbreviation, e.g. BOS.")
    return int(row[0])


def _resolve_player(conn: sqlite3.Connection, text: str, team_ids: tuple[int, ...], goalie: bool) -> sqlite3.Row:
    pos_cond = "position = 'G'" if goalie else "position <> 'G'"
    if text.isdigit():
        p: sqlite3.Row | None = conn.execute(
            f"SELECT * FROM players WHERE nhl_player_id = ? AND {pos_cond}", (int(text),)
        ).fetchone()
        if p is None:
            raise Rejected(f"No {'goalie' if goalie else 'skater'} with NHL id {text}.")
        return p
    want = _norm(text)
    marks = ",".join("?" * len(team_ids))
    rows = conn.execute(f"SELECT * FROM players WHERE {pos_cond} AND current_team_id IN ({marks})", team_ids).fetchall()
    hits: list[sqlite3.Row] = [r for r in rows if _norm(r["full_name"]) == want] or [
        r for r in rows if _norm(r["last_name"]) == want
    ]
    if len(hits) != 1:
        raise Rejected(
            f"Could not identify '{text}' ({'several matches' if hits else 'no match'} on the teams searched). "
            "Use the NHL player id."
        )
    return hits[0]


@dataclass
class Outcome:
    issue: Issue
    applied: bool
    message: str
    game_id: int | None = None
    reason: str | None = None  # projection trigger reason


def apply(conn: sqlite3.Connection, issue: Issue, source_id: int, now: datetime) -> Outcome:
    form = parse_form(issue.body)
    kind = issue.title[len(TITLE_PREFIX) :].strip().lower()
    try:
        source = _field(form, "source")
        if not URL_RE.match(source):
            raise Rejected("A source URL (http/https) is required for every entry.")
        if kind.startswith("news"):
            return _apply_news(conn, issue, form, source, source_id, now)
        team = _resolve_team(conn, _field(form, "team"))
        game = _resolve_game(conn, _field(form, "game"), team)
        teams = (game["home_team_id"], game["away_team_id"])
        matchup = conn.execute(
            "SELECT a.abbrev || ' at ' || h.abbrev FROM games g JOIN teams h ON h.id = g.home_team_id "
            "JOIN teams a ON a.id = g.away_team_id WHERE g.id = ?",
            (game["id"],),
        ).fetchone()[0]
        if kind.startswith("goalie"):
            p = _resolve_player(conn, _field(form, "goalie", "player"), (team,) if team else teams, goalie=True)
            if team is None:
                team = p["current_team_id"]
            if team not in teams:
                raise Rejected("That goalie's team is not playing in this game.")
            status = (_field(form, "status") or "confirmed").lower()
            if status not in ("confirmed", "likely"):
                raise Rejected("Status must be Confirmed or Likely.")
            conn.execute(
                "INSERT INTO goalie_starts (game_id, team_id, player_id, status, reported_at, provenance, source_id, "
                "source_ref, fetched_at) VALUES (?, ?, ?, ?, ?, 'manual', ?, ?, ?)",
                (game["id"], team, p["id"], status, issue.created_at, source_id, source, iso(now)),
            )
            msg = f"Applied: {p['full_name']} {status} to start for {matchup} on {game['game_date']}."
            return Outcome(issue, True, msg, game["id"], "goalie_confirmed")
        if kind.startswith("player"):
            p = _resolve_player(conn, _field(form, "player"), teams, goalie=False)
            status = (_field(form, "status") or "out").lower()
            status = "available" if status.startswith(("back", "avail", "in")) else "out"
            reason = _field(form, "reason").lower() or None
            if reason is not None and reason not in OUT_REASONS:
                raise Rejected(f"Reason must be one of: {', '.join(sorted(OUT_REASONS))}.")
            conn.execute(
                "INSERT INTO game_availability (game_id, player_id, status, reason, reported_at, provenance, "
                "source_id, source_ref, fetched_at) VALUES (?, ?, ?, ?, ?, 'manual', ?, ?, ?)",
                (game["id"], p["id"], status, reason, issue.created_at, source_id, source, iso(now)),
            )
            verb = "ruled out of" if status == "out" else "back in for"
            msg = f"Applied: {p['full_name']} {verb} {matchup} on {game['game_date']}."
            return Outcome(issue, True, msg, game["id"], "player_out" if status == "out" else "lineup_change")
        raise Rejected(f"Unknown Quick Entry type '{kind}'. Use one of the Quick Entry issue forms.")
    except Rejected as exc:
        return Outcome(issue, False, f"Not applied: {exc}")


def _apply_news(
    conn: sqlite3.Connection, issue: Issue, form: dict[str, str], source: str, source_id: int, now: datetime
) -> Outcome:
    """A news item with its source link, tagged to a player and/or team. Informational: it does not
    change projections (use the player-out or goalie forms for that)."""
    headline = _field(form, "headline")
    if not headline:
        raise Rejected("A headline is required.")
    category = _field(form, "category").lower() or "general"
    if category not in NEWS_CATEGORIES:
        raise Rejected(f"Category must be one of: {', '.join(sorted(NEWS_CATEGORIES))}.")
    reliability = RELIABILITY.get(_field(form, "reliability").lower(), "unverified")
    team = _resolve_team(conn, _field(form, "team"))
    player_text = _field(form, "player")
    player: sqlite3.Row | None = None
    if player_text.isdigit():
        player = conn.execute("SELECT * FROM players WHERE nhl_player_id = ?", (int(player_text),)).fetchone()
        if player is None:
            raise Rejected(f"No player with NHL id {player_text}.")
    elif player_text:
        teams = (team,) if team else tuple(r[0] for r in conn.execute("SELECT id FROM teams"))
        try:
            player = _resolve_player(conn, player_text, teams, goalie=False)
        except Rejected:
            player = _resolve_player(conn, player_text, teams, goalie=True)
    if player is None and team is None:
        raise Rejected("Tag the news with a player, a team, or both.")
    cur = conn.execute(
        "INSERT INTO news (source_id, external_id, url, headline, summary, category, reliability, published_at, "
        "fetched_at, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual')",
        (
            source_id,
            f"issue-{issue.number}",
            source,
            headline,
            _field(form, "details") or None,
            category,
            reliability,
            issue.created_at,
            iso(now),
        ),
    )
    conn.execute(
        "INSERT INTO news_entities (news_id, player_id, team_id) VALUES (?, ?, ?)",
        (cur.lastrowid, player["id"] if player is not None else None, team),
    )
    who = player["full_name"] if player is not None else _field(form, "team").upper()
    return Outcome(issue, True, f"Applied: {category} news for {who} added.")


@dataclass
class QuickEntryResult:
    reasons: dict[int, str] = field(default_factory=dict)  # game -> projection trigger reason
    outcomes: list[Outcome] = field(default_factory=list)
    retry_close: list[tuple[int, bool]] = field(default_factory=list)  # (issue, was applied)


def run_quick_entry(conn: sqlite3.Connection, tracker: IssueTracker, owner: str, now: datetime) -> QuickEntryResult:
    result = QuickEntryResult()
    source_id = register_source(conn, SOURCE)
    conn.commit()
    with ingestion_run(conn, source_id, "quick_entry") as run:
        issues = tracker.open_issues()
        for issue in issues:
            if not issue.title.startswith(TITLE_PREFIX) or issue.author.lower() != owner.lower():
                continue  # only the owner can write
            done = conn.execute("SELECT status FROM quick_entries WHERE issue_number = ?", (issue.number,)).fetchone()
            if done:
                result.retry_close.append((issue.number, done[0] == "applied"))  # closing failed last time
                continue
            out = apply(conn, issue, source_id, now)
            conn.execute(
                "INSERT INTO quick_entries (issue_number, kind, author, payload, status, message, processed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    issue.number,
                    issue.title[len(TITLE_PREFIX) :].strip(),
                    issue.author,
                    json.dumps(parse_form(issue.body)),
                    "applied" if out.applied else "rejected",
                    out.message,
                    iso(now),
                ),
            )
            result.outcomes.append(out)
            if out.applied and out.game_id is not None and out.reason:
                result.reasons[out.game_id] = out.reason
            run.rows_upserted += out.applied
            if not out.applied:
                run.errors.append(f"#{issue.number}: {out.message}")
        run.rows_read = len(issues)
    return result


def updated_counts(conn: sqlite3.Connection, game_id: int, reason: str, since: str) -> int:
    return int(
        conn.execute(
            "SELECT count(*) FROM player_projections WHERE game_id = ? AND trigger_reason = ? AND computed_at >= ?",
            (game_id, reason, since),
        ).fetchone()[0]
    )


def finish(
    conn: sqlite3.Connection, tracker: IssueTracker, result: QuickEntryResult, since: str
) -> list[Callable[[], None]]:
    """Comment/close actions to run after the store is saved. No projection values are posted."""
    actions: list[Callable[[], None]] = []
    for out in result.outcomes:
        body = out.message
        if out.applied and out.game_id is not None and out.reason:
            n = updated_counts(conn, out.game_id, out.reason, since)
            body += (
                f"\n\n{n} projections were recalculated for this game. Open the game in RinkX to see the "
                "before/after (values are only shown in the app, never here)."
            )
        body += "\n\n---\n_Processed by the RinkX pipeline._"
        num, ok = out.issue.number, out.applied

        def act(num: int = num, body: str = body, ok: bool = ok) -> None:
            tracker.comment(num, body)
            tracker.close(num, ok)

        actions.append(act)
    for num, applied in result.retry_close:

        def close(num: int = num, applied: bool = applied) -> None:
            tracker.close(num, applied)

        actions.append(close)
    return actions


def describe(result: QuickEntryResult) -> dict[str, Any]:
    return {"applied": sum(o.applied for o in result.outcomes), "rejected": sum(not o.applied for o in result.outcomes)}
