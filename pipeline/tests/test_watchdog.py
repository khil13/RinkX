"""Phase 10: the watchdog alerts once when the pipeline stops succeeding, and closes when it recovers."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

from rinkx import watchdog as wd
from rinkx.config import REPO_ROOT

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=UTC)
URL = "https://github.com/o/RinkX/actions/workflows/pipeline.yml"


class FakeRepo:
    def __init__(self, last: datetime | None):
        self.last = last
        self.issues: dict[int, dict] = {}
        self.comments: list[tuple[int, str]] = []

    def last_success(self):
        return self.last

    def open_issue(self):
        return next((n for n, i in self.issues.items() if i["open"]), None)

    def create_issue(self, title, body):
        self.issues[len(self.issues) + 1] = {"title": title, "body": body, "open": True}

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number):
        self.issues[number]["open"] = False


def test_limits_by_day_type():
    assert wd.check(NOW, NOW - timedelta(hours=1, minutes=59), True).stale is False
    assert wd.check(NOW, NOW - timedelta(hours=2, minutes=1), True).stale is True
    assert wd.check(NOW, NOW - timedelta(hours=20), False).stale is False
    assert wd.check(NOW, NOW - timedelta(hours=27), False).stale is True
    assert wd.check(NOW, NOW - timedelta(hours=3), None).stale is True  # schedule unknown: strict
    assert wd.check(NOW, None, False).stale is True  # never succeeded


def test_alert_once_then_recover():
    repo = FakeRepo(NOW - timedelta(hours=3))
    pushed: list[dict] = []
    assert wd.watch(repo, NOW, True, URL, pushed.append) == "alerted"
    issue = repo.issues[1]
    assert issue["title"] == wd.ISSUE_TITLE and "2026-11-03 20:00 UTC" in issue["body"] and URL in issue["body"]
    assert len(pushed) == 1 and pushed[0]["click"] == URL
    # Still broken an hour later: no second issue, no second push.
    assert wd.watch(repo, NOW + timedelta(hours=1), True, URL, pushed.append) == "still_stale"
    assert len(repo.issues) == 1 and len(pushed) == 1
    # A run succeeds: comment and close.
    repo.last = NOW + timedelta(hours=1, minutes=30)
    assert wd.watch(repo, NOW + timedelta(hours=2), True, URL, pushed.append) == "recovered"
    assert not repo.issues[1]["open"] and "Recovered" in repo.comments[0][1]
    assert wd.watch(repo, NOW + timedelta(hours=2), True, URL, pushed.append) == "ok"


def test_public_messages_carry_no_data():
    repo = FakeRepo(NOW - timedelta(hours=30))
    pushed: list[dict] = []
    wd.watch(repo, NOW, False, URL, pushed.append)
    text = repo.issues[1]["body"] + pushed[0]["message"]
    for leak in ("projection", "edge", "price", "passphrase", "confidence", "%"):
        assert leak not in text.lower()


def test_push_failure_still_opens_the_issue():
    repo = FakeRepo(None)

    def broken(_):
        raise OSError("down")

    assert wd.watch(repo, NOW, True, URL, broken) == "alerted" and repo.open_issue() == 1


def test_nhl_game_day_parsing():
    week = {"gameWeek": [{"date": "2026-11-03", "numberOfGames": 9}, {"date": "2026-11-04", "numberOfGames": 0}]}
    assert wd.nhl_game_day(date(2026, 11, 3), lambda url: week) is True
    assert wd.nhl_game_day(date(2026, 11, 4), lambda url: week) is False
    assert wd.nhl_game_day(date(2026, 7, 1), lambda url: {"gameWeek": []}) is False

    def down(url):
        raise OSError

    assert wd.nhl_game_day(date(2026, 11, 3), down) is None


def test_keepalive_covers_every_scheduled_workflow():
    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/check_workflows.py")], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout
    assert "watchdog.yml" in out.stdout and "pipeline.yml" in out.stdout
