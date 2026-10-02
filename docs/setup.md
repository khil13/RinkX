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
| Quick Entry issues | this repo's Issues | **yes**: they hold only what you typed and its public source link. The pipeline's reply never includes projections. |

### Sportsbook lines (Phase 4)

1. Get a free key at **the-odds-api.com** (the free plan has 500 credits a month).
2. In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `ODDS_API_KEY`, paste the key, and save.
3. Optional but recommended: run **Actions → probe-odds → Run workflow** once. It checks that the API's real responses match RinkX's parser and market list. It prints only market and bookmaker names, never prices, and costs about 9 credits.

The next pipeline run starts fetching lines for your books (`config/books.yml`: FanDuel and BetMGM). `config/budget.yml` decides how credits are spent. On the free plan that's game lines once a day plus props for about one or two games a day, soonest first. To cover every game, raise `monthly_credits` after upgrading your plan. **Admin** shows credits left and any sportsbook player names that didn't match an NHL player. Those lines stay hidden until you add the name to `config/player_aliases.yml`.

### Quick Entry (confirm a goalie, rule a player out)

On a Game page in the app, tap **Confirm goalie** or **Mark a player out**. This opens a GitHub issue form with the game already filled in. Add the player and a **source link**, then submit. Submitting starts the pipeline. A few minutes later the projections are recalculated, the app shows *Updated after goalie confirmed: before → after*, and the issue is commented on and closed. If something didn't match (an unknown player, a game that already started, no source link), the comment says why and nothing is changed.

Only issues **you** open count. Anyone else's issues are ignored and start nothing. The buttons appear only on the published site (`<you>.github.io/RinkX`). Elsewhere, open **Issues → New issue → Quick Entry** yourself.

### Changing the passphrase

Run the Setup page again with a new passphrase, but **keep your existing STORE_KEY secret unchanged**. Replace DATA_KEY with the new one and commit the new keyfile. The store is untouched because it uses STORE_KEY. Every device must unlock again.

### If something goes wrong

* **"DATA_KEY does not match keyfile.json"** in the run log: the secret and the committed keyfile came from different setups. Redo steps 3–5 with one fresh setup, keeping STORE_KEY if a store already exists.
* **"wrong STORE_KEY or corrupted store"**: the pipeline refuses to continue rather than start an empty store over your data. Restore the original STORE_KEY from your password manager.
