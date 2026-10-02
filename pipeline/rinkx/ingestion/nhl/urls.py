"""NHL API URL builders. One place, so the pipeline and the fixture recorder
(scripts/record_nhl_fixtures.py) produce byte-identical URLs."""

from __future__ import annotations

from urllib.parse import quote, urlencode

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
PAGE_SIZE = 100


def play_by_play(nhl_game_id: int) -> str:
    return f"{WEB}/gamecenter/{nhl_game_id}/play-by-play"


def shift_chart(nhl_game_id: int) -> str:
    return f"{STATS}/shiftcharts?" + urlencode({"cayenneExp": f"gameId={nhl_game_id}"}, quote_via=quote)


def game_report(entity: str, report: str, game_date: str, start: int = 0) -> str:
    """Per-game rows (isGame=true) for every player who played on `game_date`.

    `entity` is "skater" or "goalie"; `report` e.g. "summary", "timeonice", "realtime".
    Paginated: the response carries `total`; request start=0, 100, 200, ...
    """
    params = {
        "isAggregate": "false",
        "isGame": "true",
        "start": str(start),
        "limit": str(PAGE_SIZE),
        "sort": '[{"property":"playerId","direction":"ASC"},{"property":"gameId","direction":"ASC"}]',
        "cayenneExp": f'gameDate>="{game_date}" and gameDate<="{game_date} 23:59:59"',
    }
    return f"{STATS}/{entity}/{report}?" + urlencode(params, quote_via=quote)
