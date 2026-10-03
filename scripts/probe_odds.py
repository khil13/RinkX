"""Check The Odds API's real response shape against RinkX's parser, without revealing odds.

Prints bookmaker keys, market keys, outcome *field names* and side labels, quota headers, and
any market key the API rejects. Never prints prices, lines or player names (the Actions log of a
public repo is public). Costs about one credit per configured market, once.

Run: Actions -> probe-odds -> Run workflow (needs the ODDS_API_KEY secret).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from rinkx.ingestion.odds.client import BASE, redact  # noqa: E402
from rinkx.ingestion.odds.parse import OddsParseError, parse_quotes  # noqa: E402


def get(path: str, **params: str) -> tuple[object, dict[str, str]]:
    q = urllib.parse.urlencode({**params, "apiKey": os.environ["ODDS_API_KEY"]})
    url = f"{BASE}{path}?{q}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            return json.loads(r.read()), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        print(f"HTTP {e.code} for {redact(url)}: {redact(body)}")
        raise SystemExit(1) from None


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config/odds_markets.yml").read_text())
    books = [b["code"] for b in yaml.safe_load((ROOT / "config/books.yml").read_text())["books"]]
    sport = cfg["sport"]
    events, h = get(f"/sports/{sport}/events", dateFormat="iso")
    assert isinstance(events, list)
    print(f"events: {len(events)} upcoming; event fields: {sorted(events[0]) if events else []}")
    print(f"quota after events call: remaining={h.get('x-requests-remaining')} used={h.get('x-requests-used')}")
    if not events:
        return
    # Which markets each book lists, game by game (the /markets endpoint: 1 credit per game,
    # names only). A book can post points props for some games and not others, or later in the day.
    n = int(os.environ.get("PROBE_GAMES", "6"))
    listed: dict[str, dict[str, int]] = {}
    for e in events[:n]:
        mk, h = get(f"/sports/{sport}/events/{e['id']}/markets", bookmakers=",".join(books), dateFormat="iso")
        assert isinstance(mk, dict)
        per_book = {bm["key"]: sorted(m["key"] for m in bm.get("markets", [])) for bm in mk.get("bookmakers", [])}
        counts = "; ".join(f"{b}: {len(v)} markets" for b, v in per_book.items())
        print(f"game starting {e['commence_time']}: {counts}")
        for b, keys in per_book.items():
            for k in keys:
                listed.setdefault(b, {}).setdefault(k, 0)
                listed[b][k] += 1
    print(f"markets listed across the first {min(n, len(events))} games (games listing each):")
    for b, keys in sorted(listed.items()):
        print(f"  {b}: " + ", ".join(f"{k} ({c})" for k, c in sorted(keys.items())))
    print(f"quota after markets calls: remaining={h.get('x-requests-remaining')}")
    ev = events[0]
    props = [k for k in cfg["markets"] if k.startswith("player_")]
    data, h = get(
        f"/sports/{sport}/events/{ev['id']}/odds",
        markets=",".join(props),
        bookmakers=",".join(books),
        oddsFormat="american",
        dateFormat="iso",
    )
    assert isinstance(data, dict)
    print(f"event odds charged: {h.get('x-requests-last')} credits; remaining={h.get('x-requests-remaining')}")
    seen: dict[str, set[str]] = {}
    for bm in data.get("bookmakers", []):
        for mk in bm.get("markets", []):
            seen.setdefault(mk["key"], set()).add(bm["key"])
            oc = (mk.get("outcomes") or [{}])[0]
            sides = sorted(
                {o.get("name") for o in mk.get("outcomes", []) if o.get("name") in ("Over", "Under", "Yes", "No")}
            )
            print(f"  {bm['key']:10s} {mk['key']:32s} outcome fields={sorted(oc)} side labels={sides}")
    print("configured markets with no lines from these books right now:", sorted(set(props) - set(seen)) or "none")
    print("markets returned but not in config/odds_markets.yml:", sorted(set(seen) - set(cfg["markets"])) or "none")
    try:
        _, quotes = parse_quotes(data)
        print(f"parser: OK, {len(quotes)} quotes parsed")
    except OddsParseError as exc:
        print(f"parser: FAILED: {exc}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
