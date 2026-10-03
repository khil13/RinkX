# One-time setup

Everything below can be done from the GitHub iPhone app or github.com in Safari. No computer tools are needed.

> The workflows only run from the default branch, so this code must be merged into `main` first.

## 1. Turn on GitHub Pages

**Repo → Settings → Pages → Build and deployment → Source: GitHub Actions.**

## 2. Publish the setup-only site

**Actions → pipeline → Run workflow.** With no keys yet, the pipeline publishes a site that contains only the Setup page and no data. When the run finishes, open `https://khil13.github.io/RinkX/`.

## 3. Generate your keys (on the Setup page)

1. Choose a passphrase of at least 20 characters. Five or more random words is ideal. Everything on the public site is encrypted under it, and anyone can download the encrypted files and try to guess it, so length matters.
2. Tap **Generate keys**. The keys are created on your device and never sent anywhere. The page shows:
   * **DATA_KEY**: encrypts the published data
   * **STORE_KEY**: encrypts the data store
   * **config/keyfile.json**: your DATA_KEY, locked with your passphrase. It's safe to make public.
3. **Save DATA_KEY and STORE_KEY in a password manager.** GitHub never shows secret values again, and losing STORE_KEY means losing the stored data.

## 4. Add the two secrets

**Settings → Secrets and variables → Actions → New repository secret**, twice:

| Name | Value |
|---|---|
| `DATA_KEY` | from the Setup page |
| `STORE_KEY` | from the Setup page |

## 5. Commit the keyfile

Use the Setup page's **Open GitHub with the file pre-filled** link, or create `config/keyfile.json` on `main` and paste in the content. Committing it triggers the pipeline automatically.

## 6. Unlock

When the run finishes, reload the site and enter your passphrase. Tick **Remember on this device** to skip it next time. **Lock** (top right) forgets it.

---

### What each piece does

| Thing | Where | Public? |
|---|---|---|
| App code | this repo | yes |
| `config/keyfile.json` | this repo | yes, but useless without your passphrase |
| Published data (`data/*.json.enc`) | GitHub Pages | yes, but encrypted (AES-256-GCM) |
| Data store (`rinkx-*.db.enc`) | the `store` release | yes, but encrypted (AES-256-GCM, STORE_KEY) |
| DATA_KEY, STORE_KEY | Actions secrets + your password manager | **no** |
| Passphrase | your head / password manager | **no** |
| ODDS_API_KEY | Actions secret | **no** (redacted from every log and error) |
| NTFY_TOPIC | Actions secret | **no**. Anyone who knows the topic can read your alerts, so make it long and random. |
| Quick Entry issues | this repo's Issues | **yes**: they hold only what you typed and its public source link. The pipeline's reply never includes projections. |

### Sportsbook lines (Phase 4)

1. Get a free key at **the-odds-api.com** (the free plan has 500 credits a month).
2. In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `ODDS_API_KEY`, paste the key, and save.
3. Optional but recommended: run **Actions → probe-odds → Run workflow** once. It checks that the API's real responses match RinkX's parser and market list. It prints only market and bookmaker names, never prices, and costs about 9 credits.

The next pipeline run starts fetching lines for your books (`config/books.yml`: FanDuel and BetMGM). `config/budget.yml` decides how credits are spent. On the free plan that's game lines once a day plus props for about one or two games a day, soonest first. To cover every game, raise `monthly_credits` after upgrading your plan. **Admin** shows credits left and any sportsbook player names that didn't match an NHL player. Those lines stay hidden until you add the name to `config/player_aliases.yml`.

### Quick Entry (confirm a goalie, rule a player out)

On a Game page in the app, tap **Confirm goalie** or **Mark a player out**. This opens a GitHub issue form with the game already filled in. Add the player and a **source link**, then submit. Submitting starts the pipeline. A few minutes later the projections are recalculated, the app shows *Updated after goalie confirmed: before → after*, and the issue is commented on and closed. If something didn't match (an unknown player, a game that already started, no source link), the comment says why and nothing is changed.

Only issues **you** open count. Anyone else's issues are ignored and start nothing. The buttons appear only on the published site (`<you>.github.io/RinkX`). Elsewhere, open **Issues → New issue → Quick Entry** yourself.

### Alerts on your phone (Phase 8)

1. Install the **ntfy** app (iOS or Android) and subscribe to a topic with a long random name, e.g. `rinkx-` followed by 20 random letters. On ntfy.sh, anyone who knows a topic can read it, so treat the name like a password.
2. Add it as the repository secret `NTFY_TOPIC`.
3. Edit `config/alerts.yml` in the GitHub app to choose what you're alerted about. Strong leans are on by default; goalie confirmations and injury news can be switched on.

Each alert fires at most once per game. Without the secret, alerts still appear in the app under **News**, marked "not sent". Notification text never goes to the Actions logs.

### News (Quick Entry)

There is no automatic news feed. To record a news item, open **Issues → New issue → Quick Entry: news** (or **+ Add news** on the News page). Give it a headline, a category, a player and/or team, and the **source link**. It shows on the News page and the player's page, and can trigger a `news` alert. News doesn't change projections: to take a player out of the projections, use **Mark a player out**.

### On your phone (Phase 9)

Open the site in Safari (iPhone) or Chrome (Android), unlock it, then choose **Share → Add to Home Screen** (or **Install app**). It opens full screen like an app. If the phone is offline, it shows the last data it loaded. **Settings** (under More) sets decimal odds, your time zone, or a cool-off that hides every price on that phone for a while.

### Changing the passphrase

Run the Setup page again with a new passphrase, but **keep your existing STORE_KEY secret unchanged**. Replace DATA_KEY with the new one and commit the new keyfile. The store is untouched because it uses STORE_KEY. Every device must unlock again.

### Keys, backups and the watchdog (Phase 10)

- **Keep offline copies of STORE_KEY and DATA_KEY**, e.g. in your password manager and on paper in a safe place. GitHub never shows a secret again after you save it. Without STORE_KEY, no backup of the store can be read, by you or anyone.
- **Backups:** each run uploads a new encrypted version of the store. The newest 10 are kept, plus the newest of each of the last 8 weeks.
- **Restore drill:** about once a day the pipeline proves the oldest kept version still downloads, decrypts, passes SQLite's integrity check and upgrades to today's schema. The result is on **Admin → Store & backups**.
- **Rolling back:** **Actions → store → Run workflow**.
  1. Choose `list` to see the versions.
  2. Choose `restore`, paste a version name, and type `RESTORE`.
  3. That version becomes the newest. Nothing is deleted, so a restore can itself be undone the same way.
- **Watchdog:** `watchdog.yml` runs hourly, separately from the pipeline.
  - If no pipeline run has succeeded for 2 hours on an NHL game day (26 hours otherwise), it opens one issue (GitHub emails you) and pushes one ntfy notification if `NTFY_TOPIC` is set.
  - It closes the issue when a run succeeds again. It never posts data, only times.
- **Keepalive:** `keepalive.yml` re-enables every scheduled workflow weekly and fails (emailing you) if one is still disabled. CI checks that every scheduled workflow is on its list, so none can be forgotten.

### If something goes wrong

* **"DATA_KEY does not match keyfile.json"** in the run log: the secret and the committed keyfile came from different setups. Redo steps 3–5 with one fresh setup, keeping STORE_KEY if a store already exists.
* **"wrong STORE_KEY or corrupted store"**: the pipeline refuses to continue rather than start an empty store over your data. Restore the original STORE_KEY from your password manager. If the newest version itself is damaged, restore an older one (Actions → store → restore).
* **A "Watchdog" issue opened:** open the linked Actions page and read the first failing step. It closes itself once a run succeeds.
