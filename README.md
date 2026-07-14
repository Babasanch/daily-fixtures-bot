# Daily Football Predictions Bot

A Telegram bot that fetches daily fixtures (top 2 divisions worldwide, internationals,
club friendlies), scores them against your betting rules, and answers commands like
`/banker` and `/acca5` — running entirely on free-tier infrastructure.

## How it works

```
GitHub Actions (scheduled, twice daily)
    → calls API-Football
    → runs the rules engine
    → commits data/morning.json or data/evening.json back to this repo
        → Render auto-deploys on the push
            → Telegram webhook hits the redeployed bot
                → bot reads the committed JSON, replies instantly
```

**Nothing the user does (sending a command) ever calls API-Football.** All API
usage happens only in the two scheduled GitHub Actions runs, so the 100
requests/day free tier is spent predictably and the bot itself never risks
hitting a rate limit from user activity.

All session windows (morning 07:00–18:00, evening 19:00–04:00) are in
**West Africa Time (WAT, UTC+1, no DST)**.

## What this confidence score is — and isn't

Every pick comes with a 0–100 "confidence score" built from a transparent,
weighted formula (recent form, expected goals, league position gap, head-to-head,
injuries where available). It is **not** a backtested statistical probability —
it hasn't been validated against real outcomes yet. Every pick includes its
`reasoning` so you can see exactly what drove the number. If you start logging
actual results, `src/rules_engine.py` is the file to revisit for calibration.

## One-time setup

### 1. Get your API keys ready
You mentioned you already have:
- An **API-Football** account + key (from [api-sports.io](https://www.api-football.com/))
- A **Telegram bot token** (from [@BotFather](https://t.me/BotFather))

You'll also need to invent a **webhook secret** — any random string you choose
(e.g. run `python -c "import secrets; print(secrets.token_hex(16))"` locally).
This just verifies incoming webhook calls really came from Telegram.

### 2. Push this repo to GitHub
Create a new GitHub repository and push this project to it.

### 3. Add GitHub Actions secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `API_FOOTBALL_KEY` | your API-Football key |
| `API_FOOTBALL_HOST` | `v3.football.api-sports.io` (or your RapidAPI host if you signed up that way) |

### 4. Build the league whitelist (run once, locally)
This resolves country names into actual league IDs — do this once before your
first deploy so the repo has `data/league_whitelist.json` from the start:

```bash
pip install -r requirements.txt
export API_FOOTBALL_KEY=your_key_here
python -m scripts.build_league_whitelist
```

Then **open `data/league_whitelist.json` and spot-check a few countries** you
care about most — second-division detection is heuristic (based on naming
patterns like "2. Bundesliga", "Championship", "Serie B") and isn't guaranteed
perfect for every country's naming conventions. Commit the file once you're
happy with it:

```bash
git add data/league_whitelist.json
git commit -m "Add initial league whitelist"
git push
```

### 5. Deploy the bot to Render
1. Sign up at [render.com](https://render.com) (free) and connect your GitHub account.
2. New → Blueprint → select this repo. Render will read `render.yaml` automatically.
3. In the Render dashboard, set these environment variables on the service
   (they're marked `sync: false` in render.yaml, meaning Render won't auto-fill them):
   - `TELEGRAM_BOT_TOKEN` — your bot token
   - `TELEGRAM_WEBHOOK_SECRET` — the random string you invented in step 1
4. Deploy. Note your service URL, e.g. `https://football-prediction-bot.onrender.com`

**Free tier note:** Render's free web services sleep after inactivity. The
*first* message to the bot after a period of silence may take 30–60 seconds
to get a reply while it wakes up; after that it responds instantly until it
sleeps again. This is a one-time-per-nap delay, not a persistent slowdown.

### 6. Register the Telegram webhook
Run this once (replace the placeholders):

```bash
curl -F "url=https://YOUR-RENDER-URL.onrender.com/webhook/YOUR_WEBHOOK_SECRET" \
     https://api.telegram.org/botYOUR_TELEGRAM_BOT_TOKEN/setWebhook
```

You should get back `{"ok":true,"result":true,...}`.

### 7. Enable the scheduled fetch jobs
The workflows in `.github/workflows/` are already set to run automatically
(morning fetch at 06:30 WAT, evening fetch at 18:30 WAT). You can also trigger
them manually any time from the **Actions** tab → select a workflow → **Run workflow**,
which is the fastest way to test everything end-to-end without waiting for the
schedule.

### 8. Test it
Open Telegram, message your bot `/start`, then try `/homewin`, `/banker`, etc.
If a session hasn't been fetched yet, commands will say so honestly rather
than showing stale or fabricated data.

## Commands

| Command | Description |
|---|---|
| `/start` | Shows this command list |
| `/homewin` | Best home win picks, ranked |
| `/awaywin` | Best away win picks, ranked |
| `/over15` | Best Over 1.5 goals picks |
| `/over25` | Best Over 2.5 goals picks |
| `/banker` | Safest single selection, morning & evening |
| `/single` | Best single bet of the day, morning & evening |
| `/acca5` | 5-leg accumulator from the day's strongest distinct fixtures |

All strict markets only surface picks scoring 80%+ confidence. If nothing
qualifies, the bot says so rather than padding the list with weaker picks.

## Project structure

```
football-bot/
├── .github/workflows/
│   ├── fetch_morning.yml     # scheduled: 06:30 WAT
│   └── fetch_evening.yml     # scheduled: 18:30 WAT
├── src/
│   ├── config.py             # timezone, sessions, budget, league targets
│   ├── api_client.py         # budget-aware API-Football client
│   ├── fetch.py               # orchestrates fetch -> enrich -> score -> save
│   ├── rules_engine.py        # confidence scoring per market
│   ├── rank.py                 # ranking / command query helpers
│   ├── storage.py              # JSON read/write, caching
│   ├── bot.py                  # Flask webhook, Telegram command handlers
│   └── schema.py                # data shape documentation
├── scripts/
│   ├── build_league_whitelist.py
│   └── test_stage*.py          # test suites (no network/API key required)
├── data/
│   ├── league_whitelist.json   # committed
│   ├── morning.json            # committed, updated by Actions
│   ├── evening.json            # committed, updated by Actions
│   ├── team_stats_cache.json   # committed, updated by Actions
│   └── standings_cache.json    # committed, updated by Actions
├── render.yaml
└── requirements.txt
```

## Running the test suites

No API key or network access required — everything is tested against
synthetic/mocked data:

```bash
python -m scripts.test_stage2            # rules engine + ranking
python -m scripts.test_stage3_parsing    # API response parsing
python -m scripts.test_stage3_budget     # budget truncation behavior
python -m scripts.test_stage3_bot        # bot command formatting
```

## Known limitations (honest list)

- **Free-tier coverage ceiling:** ~100 API-Football requests/day. On a very
  heavy fixture day, the fetch job truncates to the earliest kickoffs and
  flags this in the bot's replies (`partial_coverage` / `coverage_note`) —
  it won't silently show incomplete data as if it were complete.
- **Corners market:** dropped, per your call — free-tier corner data
  coverage was unreliable.
- **Injuries data:** coverage varies heavily by league/country. Where
  unavailable, it's excluded from scoring (contributes 0, not guessed).
- **Confidence scores are heuristic, not backtested probabilities** — see
  above.
- **Second-division detection is naming-pattern-based** — verify
  `data/league_whitelist.json` covers what you expect.

## Phase 2 & 3 (OpenAI integration) — not yet built

Per the original plan:
- **Phase 2**: flip on OpenAI by setting `OPENAI_API_KEY` (currently wired
  as an inert config value, no behavior yet).
- **Phase 3**: `OPENAI_MODEL` toggle between `gpt-5-mini` and `gpt-5`.

These are the next stage of this build.
