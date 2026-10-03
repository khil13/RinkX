"""Parsers for The Odds API v4 payloads. Strict: anything malformed raises, never guesses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class OddsParseError(ValueError):
    pass


@dataclass(frozen=True)
class OddsEvent:
    id: str
    commence_time: str
    home_team: str
    away_team: str


@dataclass(frozen=True)
class Quote:
    book: str  # bookmaker key, e.g. 'fanduel'
    market: str  # vendor market key, e.g. 'player_shots_on_goal'
    subject: str | None  # player name for props; None for game markets
    side: str  # 'over' | 'under' | 'yes' | 'no' | 'home' | 'away'
    price: int  # American odds
    point: float | None
    last_update: str | None


def _req(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] is None:
        raise OddsParseError(f"{where}: missing '{key}'")
    return d[key]


def parse_event(d: dict[str, Any]) -> OddsEvent:
    return OddsEvent(
        str(_req(d, "id", "event")),
        str(_req(d, "commence_time", "event")),
        str(_req(d, "home_team", "event")),
        str(_req(d, "away_team", "event")),
    )


def _price(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise OddsParseError(f"{where}: price {v!r} is not a number")
    p = round(v)
    if -100 < p < 100:
        raise OddsParseError(f"{where}: {p} is not an American price (did the format change?)")
    return p


def parse_quotes(event: dict[str, Any]) -> tuple[OddsEvent, list[Quote]]:
    """All quotes in one event payload (game odds or event odds)."""
    ev = parse_event(event)
    quotes: list[Quote] = []
    for bm in event.get("bookmakers") or []:
        book = str(_req(bm, "key", "bookmaker"))
        for mk in bm.get("markets") or []:
            market = str(_req(mk, "key", f"{book} market"))
            where = f"{book}/{market}"
            updated = mk.get("last_update") or bm.get("last_update")
            for oc in mk.get("outcomes") or []:
                name = str(_req(oc, "name", where))
                price = _price(_req(oc, "price", where), where)
                point = oc.get("point")
                if point is not None and not isinstance(point, (int, float)):
                    raise OddsParseError(f"{where}: point {point!r} is not a number")
                desc = oc.get("description")
                low = name.lower()
                if market == "h2h":
                    if name == ev.home_team:
                        side = "home"
                    elif name == ev.away_team:
                        side = "away"
                    else:
                        raise OddsParseError(f"{where}: outcome {name!r} is neither team")
                    quotes.append(Quote(book, market, None, side, price, None, updated))
                elif low in ("over", "under"):
                    quotes.append(
                        Quote(book, market, desc, low, price, float(point) if point is not None else None, updated)
                    )
                elif low in ("yes", "no") and market.endswith("_alternate") and point is not None:
                    # A ladder rung ("2+ points") labelled Yes: the same as Over at that point.
                    if not desc:
                        raise OddsParseError(f"{where}: ladder outcome without a player")
                    side = "over" if low == "yes" else "under"
                    quotes.append(Quote(book, market, str(desc), side, price, float(point), updated))
                elif low in ("yes", "no"):
                    if not desc:
                        raise OddsParseError(f"{where}: yes/no outcome without a player")
                    quotes.append(Quote(book, market, str(desc), low, price, None, updated))
                elif market.startswith("player_") and point is None:
                    # Scorer markets may list the player as the outcome name (a "Yes" price).
                    quotes.append(Quote(book, market, name, "yes", price, None, updated))
                else:
                    raise OddsParseError(f"{where}: unrecognised outcome {name!r}")
    return ev, quotes
