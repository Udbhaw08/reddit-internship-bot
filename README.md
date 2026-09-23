# Reddit Internship Finder

Auto-checks Reddit every 30 minutes for internship posts matching your
profile (full stack, backend, AI agent, machine learning, computer
vision), sends new ones to Telegram, and keeps a live dashboard on
GitHub Pages. Free — no paid tier, no server to maintain.

Runs entirely on GitHub Actions, so it works even when your laptop is off.

**Important update (Reddit's Responsible Builder Policy, updated June
2026):** registering an app at reddit.com/prefs/apps is no longer
enough by itself — Reddit now requires you to separately *request and
receive approval* before using its Data API, even for personal,
non-commercial projects. So this project works in two stages:

- **Right now, with zero waiting:** it runs on Reddit's public RSS
  feeds (no login/API key needed). This is the default until you have
  OAuth approval.
- **Once Reddit approves your Data API access:** just add the
  credentials as secrets (below) and the script automatically switches
  to the official API — more reliable and higher rate limits.

Both paths use the exact same script and workflow — nothing to rebuild.

---

## Part A — Get it running today (RSS mode, no approval needed)

1. Push this folder to a new **public** GitHub repo (public = unlimited
   free Actions minutes).
2. Create a free Telegram bot:
   - Message **@BotFather** on Telegram → `/newbot` → follow prompts →
     you get a **bot token**.
   - Message your new bot once (search its username, hit Start).
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a
     browser → find `"chat":{"id": ...}` → that's your **chat_id**.
3. Add these two secrets: repo → **Settings → Secrets and variables →
   Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   (Leave the `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` secrets unset for
   now — the script will automatically use RSS instead.)
4. Enable GitHub Pages: **Settings → Pages** → Source: **Deploy from a
   branch** → Branch `main`, folder `/docs` → Save. Dashboard goes live
   at `https://<your-username>.github.io/<repo-name>/`.
5. Test it: **Actions** tab → **"Fetch Internship Posts"** → **Run
   workflow**. Check the run log — it should say
   `"No REDDIT_CLIENT_ID/SECRET set — using RSS feeds."`

**Caveat, honestly:** Reddit's RSS feeds don't need a login, but Reddit
does rate-limit/block requests from datacenter IPs (which is what
GitHub Actions runs on) more aggressively than from a home connection.
Most of the time this works fine for a handful of subreddits checked
every 30 minutes — but if a run's log shows RSS requests failing
repeatedly, that's Reddit throttling the runner's IP, not a bug in the
script. It'll just quietly retry next cycle. If it's unreliable for
you, that's the signal to prioritize Part B.

---

## Part B — Get official API access (more reliable, needs approval)

### 1. Register the app (free, instant)
1. Go to https://reddit.com/prefs/apps → **create app**
2. Type: **script**. Redirect URI: `http://localhost:8080` (unused but
   required).
3. Note the **client_id** (short string under the app name) and
   **client_secret**.

### 2. Request Data API access approval (free, but takes time)
Registering the app above does **not** grant API access by itself.
Separately:
- Go to https://support.reddithelp.com/hc/en-us/requests/new
- File a request for **Data API access**, explaining it's a personal,
  non-commercial project (a self-use internship-listings dashboard —
  no reposting, no commercial use, no AI training).
- Wait for approval — developers report anywhere from a few days to a
  couple of weeks for personal/non-commercial requests.

### 3. Once approved, add the remaining secrets
Same place as before (**Settings → Secrets and variables → Actions**):
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT` — e.g. `internship-finder-bot/1.0 by u/your_reddit_username`

Next scheduled run will automatically pick these up and switch to the
official API (you'll see `"OAuth token acquired"` in the log instead of
the RSS message). No code changes needed.

---

## Customizing

Open `fetch_jobs.py`:
- `SUBREDDITS` — add/remove subreddits to search
- `ROLE_KEYWORDS` — add/remove keywords for your target roles
- `RETENTION_DAYS` — how long posts stay on the dashboard (default 30)

## Notes
- This is read-only, personal, non-commercial use only — matches
  Reddit's policy for the free tier.
- Public repos get unlimited free GitHub Actions minutes; each run
  takes under a minute.
