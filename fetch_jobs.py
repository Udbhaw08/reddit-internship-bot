#!/usr/bin/env python3
"""
Reddit Internship Finder
-------------------------
Searches a list of subreddits for internship posts matching Udbhaw's
target roles (full stack, backend, AI agent, ML, computer vision),
sends new matches to Telegram, and rebuilds a static dashboard
(docs/index.html) for GitHub Pages.

Two fetch modes, tried in this order:
  1. OAuth (Reddit's official Data API) — used automatically if
     REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set AND Reddit has
     approved API access for them (see README — approval is required
     separately from just registering an app, per Reddit's Responsible
     Builder Policy, updated June 2026).
  2. Public RSS feeds (no auth needed) — automatic fallback while
     waiting for OAuth approval, or if no credentials are set at all.
     Note: Reddit can rate-limit/block RSS requests from datacenter
     IPs (which is what GitHub Actions runs on) more aggressively than
     from a home IP. If RSS also gets blocked, the run just logs a
     warning and produces no new results that cycle — it will keep
     retrying on the next scheduled run.
"""

import os
import json
import time
import html
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ---------- Configuration ----------

DEFAULT_SUBREDDITS = [
    "forhire",
    "hiring",
    "jobbit",
    "remotejobs",
    "MachineLearningJobs",
    "developersIndia",
    "freelance_forhire",
    "techjobs",
    "AIJobs",
    "RemotePython",
    "internships",
]

env_subs = os.environ.get("SUBREDDITS")
SUBREDDITS = [s.strip() for s in env_subs.split(",") if s.strip()] if env_subs else DEFAULT_SUBREDDITS

# Negative disqualifiers: people seeking work, resume critiques, discussions, advice, questions
EXCLUDE_PATTERNS = [
    r'\[for\s*hire\]',
    r'\(for\s*hire\)',
    r'\bfor\s*hire\b',
    r'\bhire\s*me\b',
    r'\bavailable\s+for\s+hire\b',
    r'\bopen\s+to\s+work\b',
    r'\blooking\s+for\s+(a\s+)?(job|internship|internships|work|role|mentor|guidance)\b',
    r'\bseeking\s+(a\s+)?(job|internship|internships|work|role|opportunity)\b',
    r'\bin\s+search\s+of\b',
    r'\bneed\s+(a\s+)?(job|internship)\b',
    r'\b(rate|roast|review)\s+my\s+resume\b',
    r'\bresume\s+(review|critique|roast)\b',
    r'\bask\s+me\s+anything\b',
    r'\bama\b',
    r'\bhow\s+(to|do\s+i|can\s+i|did\s+you)\b',
    r'\bwhich\s+(sites|companies|platforms|courses)\b',
    r'\bwhat\s+(to\s+do|should\s+i|is\s+the\s+best)\b',
    r'\bany\s+(advice|tips|suggestions|recommendations)\b',
    r'\bgot\s+rejected\b',
    r'\brejections?\b',
    r'\bcleared\s+genc\b',
]

# Positive hiring signals: tags or phrases proving an employer/recruiter is actively hiring
HIRING_SIGNALS = [
    r'\[hiring\]',
    r'\(hiring\)',
    r'\{hiring\}',
    r'\bhiring\b',
    r'\bwe(\x27re|\x92re|\x27re|\s+are)\s+hiring\b',
    r'\bis\s+hiring\b',
    r'\burgently\s+hiring\b',
    r'\b(job|internship|intern)\s+(opening|openings|opportunity|opportunities)\b',
    r'\b(looking\s+to\s+hire|wanted)\b',
    r'\bpaid\s+internship\b',
    r'\bstipend\b',
    r'(\$|usd|inr|rs\.?|eur|£)\s*\d+',
    r'\d+\s*(usd|eur|inr|k|\/hr|\/hour|\/mo|\/month)\b',
]

# Role / Tech keywords: AI, ML, SWE, Full Stack, Backend, Python, etc.
ROLE_PATTERNS = [
    r'\bfull\s*stack\b',
    r'\bfullstack\b',
    r'\bbackend\b',
    r'\bback\s*end\b',
    r'\bfrontend\b',
    r'\bfront\s*end\b',
    r'\bweb\s*dev(eloper)?\b',
    r'\bsoftware\s*(engineer|developer|engineering)\b',
    r'\bswe\b',
    r'\bdeveloper\b',
    r'\bprogrammer\b',
    r'\bai\b',
    r'\bai\s*agent(ic)?\b',
    r'\bagentic\b',
    r'\bmachine\s*learning\b',
    r'\bml\b',
    r'\bmlops\b',
    r'\bcomputer\s*vision\b',
    r'\bdeep\s*learning\b',
    r'\bpython\b',
    r'\bllm\b',
    r'\bgenai\b',
    r'\bgenerative\s*ai\b',
    r'\bai\s*(evaluator|trainer|training)\b',
    r'\bdata\s*scien(ce|tist)\b',
    r'\bnlp\b',
    r'\bintern(ship)?\b',
]

# Dedicated job boards where every post is an employer job listing
DEDICATED_JOB_SUBS = {
    'machinelearningjobs',
    'aijobs',
    'techjobs',
    'remotepython',
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")
SEEN_FILE = os.path.join(DATA_DIR, "seen.json")

RETENTION_DAYS = 30

REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


# ---------- Helpers ----------

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def matches_filters(title, selftext="", subreddit=""):
    t_title = title.lower()
    t_all = f"{title} {selftext}".lower()

    # 1. Negative Disqualifiers: Candidate looking for work, questions, resume reviews, advice
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, t_title):
            return False

    # If title contains question mark without an explicit [hiring] tag, it's a discussion/question
    if "?" in t_title and not any(re.search(p, t_title) for p in [r'\[hiring\]', r'\bhiring\b', r'\bwe(\x27re|\s+are)\s+hiring\b']):
        return False

    # 2. Positive Hiring Signals: Must be an employer/recruiter offering a job/internship
    is_job_board = subreddit.lower() in DEDICATED_JOB_SUBS
    has_hiring_signal = any(re.search(pat, t_title) for pat in HIRING_SIGNALS)
    if not (has_hiring_signal or is_job_board):
        return False

    # 3. Role Keywords: Must match targeted tech/AI/developer roles
    has_role = any(re.search(pat, t_all) for pat in ROLE_PATTERNS)
    if not has_role:
        return False

    return True


# ---------- OAuth (official Data API) ----------

def get_reddit_token():
    auth = requests.auth.HTTPBasicAuth(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=auth,
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": REDDIT_USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_subreddit_oauth(subreddit, token, limit=50):
    headers = {"Authorization": f"bearer {token}", "User-Agent": REDDIT_USER_AGENT}
    url = f"https://oauth.reddit.com/r/{subreddit}/new"
    resp = requests.get(url, headers=headers, params={"limit": limit}, timeout=15)
    if resp.status_code != 200:
        print(f"  [warn] OAuth r/{subreddit} returned {resp.status_code}, skipping")
        return []
    posts = []
    for child in resp.json().get("data", {}).get("children", []):
        d = child.get("data", {})
        posts.append({
            "id": d.get("id"),
            "title": d.get("title", ""),
            "selftext": d.get("selftext", ""),
            "subreddit": subreddit,
            "permalink": "https://reddit.com" + d.get("permalink", ""),
            "created_utc": d.get("created_utc", time.time()),
        })
    return posts


# ---------- RSS fallback (no auth needed) ----------

def fetch_subreddit_rss(subreddit, limit=50):
    url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
    headers = {"User-Agent": REDDIT_USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        print(f"  [warn] RSS r/{subreddit} request failed: {e}")
        return []
    if resp.status_code == 429:
        time.sleep(2.5)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except Exception as e:
            print(f"  [warn] RSS r/{subreddit} retry failed: {e}")
            return []
    if resp.status_code != 200:
        print(f"  [warn] RSS r/{subreddit} returned {resp.status_code}, skipping")
        return []
    posts = []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"  [warn] RSS r/{subreddit} parse failed: {e}")
        return []

    for entry in root.findall("atom:entry", ATOM_NS)[:limit]:
        title_el = entry.find("atom:title", ATOM_NS)
        link_el = entry.find("atom:link", ATOM_NS)
        id_el = entry.find("atom:id", ATOM_NS)
        published_el = entry.find("atom:published", ATOM_NS)
        content_el = entry.find("atom:content", ATOM_NS)

        title = title_el.text if title_el is not None else ""
        permalink = link_el.attrib.get("href") if link_el is not None else ""
        raw_id = id_el.text if id_el is not None else permalink
        selftext = content_el.text if content_el is not None and content_el.text else ""

        created_utc = time.time()
        if published_el is not None and published_el.text:
            try:
                ts = published_el.text.replace("Z", "+00:00")
                created_utc = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                pass

        posts.append({
            "id": raw_id,
            "title": title,
            "selftext": selftext,
            "subreddit": subreddit,
            "permalink": permalink,
            "created_utc": created_utc,
        })
    return posts


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [info] Telegram not configured, skipping alert")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"  [warn] telegram send returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"  [warn] telegram send failed: {e}")


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internship Finder Dashboard</title>
<style>
  :root {{
    --bg: #0f1115; --card: #171a21; --text: #e8e9ec; --muted: #9aa0ab;
    --accent: #7dd3fc; --border: #262a33;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px 16px 60px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 20px; }}
  .filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }}
  .filters input {{
    flex: 1; min-width: 200px; padding: 10px 12px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 0.95rem;
  }}
  .count {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 12px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; margin-bottom: 10px;
  }}
  .card a {{ color: var(--accent); text-decoration: none; font-weight: 600; font-size: 1rem; }}
  .card a:hover {{ text-decoration: underline; }}
  .meta {{ color: var(--muted); font-size: 0.8rem; margin-top: 6px; }}
  .empty {{ color: var(--muted); text-align: center; padding: 40px 0; }}
</style>
</head>
<body>
  <h1>Internship Finder</h1>
  <div class="sub">Auto-updated from Reddit &middot; last run: {last_run} &middot; source: {source}</div>
  <div class="filters">
    <input id="search" type="text" placeholder="Filter by keyword (e.g. backend, MLOps, remote)...">
  </div>
  <div class="count" id="count"></div>
  <div id="list"></div>

  <script>
    const posts = {posts_json};

    const list = document.getElementById('list');
    const search = document.getElementById('search');
    const count = document.getElementById('count');

    function escapeHtml(text) {{
      const div = document.createElement('div');
      div.textContent = text || '';
      return div.innerHTML;
    }}

    function render(filter) {{
      const f = (filter || '').toLowerCase();
      const filtered = posts.filter(p =>
        (p.title + ' ' + p.subreddit).toLowerCase().includes(f)
      );
      count.textContent = filtered.length + ' openings';
      list.innerHTML = filtered.length ? filtered.map(p => `
        <div class="card">
          <a href="${{escapeHtml(p.url)}}" target="_blank" rel="noopener">${{escapeHtml(p.title)}}</a>
          <div class="meta">r/${{escapeHtml(p.subreddit)}} &middot; ${{escapeHtml(p.date)}}</div>
        </div>
      `).join('') : '<div class="empty">No matching posts yet.</div>';
    }}

    search.addEventListener('input', () => render(search.value));
    render('');
  </script>
</body>
</html>
"""


def build_dashboard(posts, source):
    os.makedirs(DOCS_DIR, exist_ok=True)
    posts_for_js = [
        {
            "title": p["title"],
            "url": p["permalink"],
            "subreddit": p["subreddit"],
            "date": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).strftime("%d %b %Y"),
        }
        for p in sorted(posts, key=lambda x: x["created_utc"], reverse=True)
    ]
    html = DASHBOARD_TEMPLATE.format(
        last_run=datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
        source=source,
        posts_json=json.dumps(posts_for_js, ensure_ascii=False),
    )
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ---------- Main ----------

def main():
    token = None
    source = "RSS (public feeds, no auth)"

    if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
        try:
            print("Requesting Reddit OAuth token...")
            token = get_reddit_token()
            source = "Reddit Data API (OAuth)"
            print("OAuth token acquired — using the official Data API.")
        except Exception as e:
            print(f"  [warn] OAuth failed ({e}). This usually means Reddit hasn't "
                  f"approved Data API access for this app yet (see README). "
                  f"Falling back to RSS feeds for this run.")
    else:
        print("No REDDIT_CLIENT_ID/SECRET set — using RSS feeds.")

    seen = set(load_json(SEEN_FILE, []))
    all_posts = {p["id"]: p for p in load_json(POSTS_FILE, [])}

    new_matches = []

    for sub in SUBREDDITS:
        print(f"Checking r/{sub} ...")
        if token:
            raw_posts = fetch_subreddit_oauth(sub, token)
        else:
            raw_posts = fetch_subreddit_rss(sub)

        for p in raw_posts:
            if not p.get("id") or not matches_filters(p["title"], p.get("selftext", ""), sub):
                continue
            all_posts[p["id"]] = {
                "id": p["id"],
                "title": p["title"],
                "subreddit": p["subreddit"],
                "permalink": p["permalink"],
                "created_utc": p["created_utc"],
            }
            if p["id"] not in seen:
                new_matches.append(all_posts[p["id"]])
                seen.add(p["id"])

        time.sleep(2)  # be polite / avoid tripping rate limits

    cutoff = time.time() - RETENTION_DAYS * 86400
    all_posts = {pid: p for pid, p in all_posts.items() if p["created_utc"] >= cutoff}

    save_json(POSTS_FILE, list(all_posts.values()))
    save_json(SEEN_FILE, list(seen))
    build_dashboard(list(all_posts.values()), source)

    print(f"Found {len(new_matches)} new matching post(s) via {source}.")
    for p in new_matches:
        safe_title = html.escape(p["title"])
        msg = f"💼 <b>{safe_title}</b>\n\n📍 Community: <b>r/{p['subreddit']}</b>\n🔗 <a href=\"{p['permalink']}\">View &amp; Apply on Reddit &rarr;</a>"
        send_telegram(msg)

    print("Done.")


if __name__ == "__main__":
    main()
