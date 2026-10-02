import { useState } from "react";
import { Link } from "react-router";
import { QuickEntryLink } from "../components/Projections";
import { Notice, Panel, Spinner } from "../components/ui";
import { useEncrypted } from "../lib/data/fetch";
import type { AlertsHistory, NewsFeed, NewsItem } from "../lib/data/types";
import { localTime } from "../lib/format";

const RELIABILITY: Record<NewsItem["reliability"], string> = {
  official: "official",
  beat_reporter: "beat reporter",
  aggregator: "aggregator",
  unverified: "unverified",
};

const CATEGORY_STYLE: Partial<Record<NewsItem["category"], string>> = {
  injury: "border-bad/40 text-bad",
  scratch: "border-warn/40 text-warn",
  suspension: "border-bad/40 text-bad",
};

export function NewsList({ items }: { items: NewsItem[] }) {
  return (
    <ul className="flex flex-col divide-y divide-line" aria-label="News">
      {items.map((n) => (
        <li key={n.id} className="flex flex-col gap-1 py-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span
              className={`rounded border px-1 text-[10px] font-semibold uppercase ${CATEGORY_STYLE[n.category] ?? "border-line text-muted"}`}
            >
              {n.category}
            </span>
            {n.player && (
              <Link to={`/players/${n.player.id}`} className="text-accent hover:underline">
                {n.player.name}
              </Link>
            )}
            {n.team && <span className="num text-muted">{n.team}</span>}
            <span className="num text-muted">· {localTime(n.published_at)}</span>
          </div>
          <a href={n.url} target="_blank" rel="noreferrer" className="text-sm font-semibold hover:text-accent">
            {n.headline}
          </a>
          {n.summary && <p className="text-xs text-muted">{n.summary}</p>}
          <p className="text-[11px] text-muted">
            Source: {new URL(n.url).hostname} ({RELIABILITY[n.reliability]})
          </p>
        </li>
      ))}
    </ul>
  );
}

const DELIVERY: Record<AlertsHistory["events"][number]["delivery"], string> = {
  sent: "sent",
  failed: "delivery failed",
  pending: "not sent",
};

function Alerts({ a }: { a: AlertsHistory }) {
  return (
    <Panel title="Alerts">
      <p className="mb-2 text-xs text-muted">
        {a.delivery
          ? "Alerts are pushed to your phone through ntfy."
          : "Push delivery isn't set up (add the NTFY_TOPIC secret), so alerts are only listed here."}{" "}
        Each alert fires at most once per game. Edit them in <code>config/alerts.yml</code>.
      </p>
      {a.alerts.length > 0 && (
        <ul className="mb-3 flex flex-wrap gap-2 text-xs" aria-label="Configured alerts">
          {a.alerts.map((x) => (
            <li key={x.key} className={`rounded border px-1.5 py-0.5 ${x.active ? "border-line" : "border-line text-muted line-through"}`}>
              {x.key} <span className="text-muted">· {x.type}</span>
            </li>
          ))}
        </ul>
      )}
      {a.events.length === 0 ? (
        <p className="text-sm text-muted">No alerts have fired in the last 14 days.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line" aria-label="Fired alerts">
          {a.events.map((e) => (
            <li key={`${e.key}-${e.game}`} className="flex flex-col gap-1 py-2">
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span>
                  <span className="font-semibold">{e.key}</span>{" "}
                  <Link to={`/games/${e.game}`} className="text-accent hover:underline">
                    {e.matchup}
                  </Link>
                </span>
                <span className="num text-muted">
                  {localTime(e.triggered_at)} · {DELIVERY[e.delivery]}
                </span>
              </div>
              <p className="whitespace-pre-line text-sm">{e.message}</p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function News() {
  const news = useEncrypted<NewsFeed>("news.json");
  const alerts = useEncrypted<AlertsHistory>("alerts.json");
  const [cat, setCat] = useState("");
  if (news.state === "loading" || alerts.state === "loading") return <Spinner label="Loading news…" />;
  if (news.state === "error") return <Notice tone="bad">{news.error.message}</Notice>;
  const items = news.state === "ready" ? news.value.data.items : [];
  const shown = cat ? items.filter((n) => n.category === cat) : items;
  const cats = Array.from(new Set(items.map((n) => n.category))).sort();

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">News &amp; alerts</h1>
          <p className="text-sm text-muted">
            News you entered through Quick Entry, each with its source, and the alerts that fired.
          </p>
        </div>
        <QuickEntryLink template="news" fields={{}}>
          + Add news
        </QuickEntryLink>
      </header>

      <Panel
        title="News"
        right={
          cats.length > 1 ? (
            <select
              aria-label="Category"
              value={cat}
              onChange={(e) => setCat(e.target.value)}
              className="min-h-9 rounded border border-line bg-panel px-2 text-xs"
            >
              <option value="">All categories</option>
              {cats.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          ) : undefined
        }
      >
        {items.length === 0 ? (
          <p className="text-sm text-muted">
            No news in the last {news.state === "ready" ? news.value.data.days : 14} days. There is no automatic news
            feed: items appear here when you add them with the “Quick Entry: news” form, always with a source link.
          </p>
        ) : (
          <NewsList items={shown} />
        )}
      </Panel>

      {alerts.state === "ready" && <Alerts a={alerts.value.data} />}
    </div>
  );
}
