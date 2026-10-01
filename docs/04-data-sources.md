# 04 · Data-Source Strategy

**Scope: personal use.** RinkX is a private, single-user tool and redistributes nothing. That fits the personal / non-commercial terms of the free sources below. It does **not** make scraping acceptable where a site's terms forbid it.

**Policy.** No scraping that violates a site's terms of service or robots.txt. Each source is registered in `data_sources` with its tier and license notes, and gets an adapter in `backend/rinkx/ingestion/adapters/` that can be disabled with a flag. Terms change, so **re-check each source's current terms before enabling it in production**. The notes below reflect the general situation as of this writing, not legal advice.

If a needed category has no compliant source, the app shows **"Data unavailable"** for it. It never fills the gap with guesses.

## Summary matrix

| Need | Free | Paid | Optional / manual |
|---|---|---|---|
| Schedules, scores, rosters | **NHL Web API** | Sportradar NHL, SportsDataIO | — |
| Box-score stats, TOI splits | **NHL Web + Stats APIs** | Sportradar, SportsDataIO | — |
| Play-by-play / shot locations | **NHL Web API** (play-by-play feed) | Sportradar | NHL EDGE tracking data, if exposed |
| Advanced stats (xG, CF, HD) | **Computed in-house** from PBP; **MoneyPuck** downloadable data (attribution) | Evolving-Hockey (subscription, research use) | Natural Stat Trick (manual research only, no scraping) |
| Line combinations & PP units | Derived from NHL shift charts (actual deployment, post-game) | Licensed lineup feed (e.g. Daily Faceoff partnership, SportsDataIO, Rotowire) | Admin entry with mandatory source URL |
| Starting goalies | NHL game feed once the game starts (actual) | Licensed feed (Daily Faceoff / Rotowire / SportsDataIO) | Admin entry from official team announcements, with URL |
| Injuries / scratches / suspensions | NHL roster status (limited); NHL Department of Player Safety announcements (official) | Sportradar, SportsDataIO, Rotowire news feed | Admin entry with URL |
| Prop lines (multi-book) | — | **The Odds API**, OpticOdds, SportsGameOdds, OddsJam API, Sportradar Odds | — |
| Game lines (ML, total) | — | Same odds vendor | — |
| Historical odds / closing lines | Our own snapshots from day one | The Odds API historical endpoints (paid plans), OpticOdds / SportsGameOdds historical | — |
| Venue coordinates | Public geodata (compiled once, static file) | — | — |

## Free sources

### NHL Web API — `api-web.nhle.com/v1` and `api.nhle.com/stats/rest`
* **Provides:** schedule by date, standings, rosters, player landing pages and game logs, boxscores with TOI and PP/SH splits, play-by-play with coordinates and strength state, and shift charts (`/stats/rest/en/shiftcharts`). Stats REST also covers skater/goalie/team summaries, realtime stats (hits, blocks) and TOI breakdowns.
* **Caveats:** public but **undocumented**, with no SLA, and endpoints change between seasons. NHL data is the league's property. Personal, non-commercial use with polite request rates is the intended posture. If RinkX ever became shared or commercial, it would need a licensed provider (Sportradar is the NHL's official data partner).
* **Engineering:** raw payloads are kept (compressed, encrypted) as Release assets for replay. We rate-limit politely (≤ 2 req/s), cache aggressively, and run contract tests that alert when a response shape changes.

### MoneyPuck data downloads
* **Provides:** shot-level data with xG, season skater/goalie/team/line tables back to 2008.
* **Caveats:** free to use with attribution under MoneyPuck's stated terms, which covers personal use. The attribution is shown in the app footer. Used for **historical backfill and xG validation**. The long-term plan is an in-house xG model so live inference doesn't depend on a third party.

### In-house derivations (no third-party dependency)
* xG model (logistic regression / GBM on shot distance, angle, type, rebound, rush and strength), trained on NHL PBP.
* Corsi/Fenwick, high-danger counts, on-ice rates, rest/travel, and actual line deployment reconstructed from shift charts.
* Arena scorekeeper bias factors for hits, blocks and SOG, estimated from home/away differentials.

### Moneylines inside the NHL schedule (found while building Phase 1)
The NHL schedule endpoint carries **game moneylines from the NHL's betting partners** (an `odds` array per team plus an `oddsPartners` list), including DraftKings (US) and FanDuel (Canada). This is a free, legitimate source for *game* moneylines and therefore for game-environment inputs, but it has **no totals and no player props**. It is planned as a Phase 4 input next to a paid odds feed.

## Paid sources (recommended budget order)

1. **Odds vendor (required for any market feature).** Recommendation: **The Odds API** as the starting point. It covers NHL player props (shots on goal, goals, assists, points, PP points, blocked shots, saves, anytime/first goal scorer) for US books on paid tiers, and offers historical snapshots. Credits are metered per market × region, which is why polling cadence is quota-aware. Alternatives with deeper prop coverage and lower latency, at higher cost: **OpticOdds**, **SportsGameOdds**, **OddsJam API**. Hits props in particular are offered by fewer books and vendors, so expect "Data unavailable" more often.
2. **Lineups + starting goalies + injuries/news.** This is the most important accuracy input after odds. Data partnerships (e.g. Daily Faceoff) aren't realistic for one person, so the personal-use plan is:
   * **Default (free):** **Quick Entry** via GitHub Issue forms, from the GitHub iPhone app or a button in RinkX. Paste the official source URL (team PR account, NHL.com, a beat reporter) and pick goalie / scratch / line change / injury. It takes about 10 seconds per item, and the source and timestamp are stored as for any other row. Post-game lines are reconstructed automatically from NHL shift charts.
   * **Optional paid upgrade:** a **SportsDataIO** or **Rotowire** subscription tier that allows personal use, if manual entry gets tedious. Daily Faceoff's site is still not scraped.
3. **Sportradar NHL:** not needed for personal use. Listed only as the path if the project ever went commercial.
4. **Evolving-Hockey** (subscription): reference for validating our own GAR-style and xG numbers. Its terms restrict redistribution, so it is **never served in the app**.

## Optional / manual

* **Natural Stat Trick, HockeyViz, AllThreeZones:** valuable for research, but no API, and automated collection would violate their terms. Used only manually by the modeling team for sanity checks, or under explicit permission.
* **Official team and league announcements** (press releases, official team social accounts): you can enter items through a Quick Entry issue form, which requires a URL. Social media APIs (X/Bluesky) can be added later under their API terms.
* **NHL EDGE** puck and player tracking: integrate if and when it's available through a sanctioned endpoint.

## MVP recommendation

| Phase | Sources | Approx. monthly cost |
|---|---|---|
| 1–3 (no markets) | NHL Web API, MoneyPuck, in-house derivations | $0 |
| 4–6 (markets) | + The Odds API paid tier sized to quota math (see below) | vendor-dependent, typically tens to low hundreds of USD |
| Optional | + Paid lineups/goalies/injuries feed, if manual Quick Entry gets tedious | vendor quote |

**Quota math for odds** (to size the plan): roughly 8 games/day × 10 prop markets × 1 region = 80 credits per refresh. At 12 refreshes/day that's ~960/day, or ~29k/month. Event-level props are billed per event and market, so check the vendor's current pricing formula before buying a tier.

For personal use, fit the cadence to the cheapest tier rather than the other way round. Fewer refreshes (e.g. 4–6 per day, concentrated in the last 3 hours before puck drop), only the markets you actually research, and only the books you can actually bet at bring usage to a fraction of the figure above. The scheduler takes a monthly credit budget and spreads refreshes to stay inside it.

## Ingestion contract

Every adapter implements:

```python
class SourceAdapter(Protocol):
    code: str                                  # matches data_sources.code
    def fetch(self, window: FetchWindow) -> RawPayload: ...      # raw JSON/CSV, stored to object storage
    def parse(self, raw: RawPayload) -> list[Record]: ...        # typed, validated (Pydantic)
    def upsert(self, records: list[Record], db) -> ChangeSet: ... # idempotent; returns diffs -> change events
```

Every record must carry `source_id`, `source_ref`, `fetched_at`, `provenance` and `quality`, or validation rejects it. Parse failures go to `data_quality_issues`. They never become silent zeros.

### Entity resolution
Player names differ across vendors ("Mitch Marner" vs "Mitchell Marner"). Odds vendors key on names, and the NHL uses numeric IDs. A `player_aliases` mapping table (added in Phase 4) resolves vendor names to `players.id` using exact match, then a curated alias table, then fuzzy match restricted to the game's two rosters. A fuzzy match below the confidence threshold goes to an admin review queue. Unresolved lines are **not shown**, so a line is never attached to the wrong player.
