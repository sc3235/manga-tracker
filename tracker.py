"""Check manga sites for new chapters and notify via ntfy."""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
SITES_FILE = ROOT / "sites.json"
STATE_FILE = ROOT / "state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}
TIMEOUT = 20
DELAY_BETWEEN_SITES = 2


def get(url):
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


# --- Fetchers: each returns {"id", "title", "url"} for the latest chapter ---


def fetch_rss(site):
    feed = feedparser.parse(get(site["url"]).content)
    if not feed.entries:
        raise ValueError("feed has no entries")
    entries = feed.entries
    if all(e.get("published_parsed") for e in entries):
        entries = sorted(entries, key=lambda e: e.published_parsed, reverse=True)
    latest = entries[0]
    return {
        "id": latest.get("id") or latest.link,
        "title": latest.title,
        "url": latest.link.split("?")[0],
    }


def fetch_comicnettai(site):
    # Chapters are listed newest first. The viewer link carries an encrypted,
    # per-request token, so use the content ID from the thumbnail path instead
    # and link the notification to the series page.
    soup = BeautifulSoup(get(site["url"]).text, "html.parser")
    item = soup.select_one("a.detail--product__item")
    if item is None:
        raise ValueError("no chapter items found")
    title = item.select_one(".detail--product__item__title").get_text(strip=True)
    img = item.select_one("img")
    match = re.search(r"/book_contents/(\d+)/", img.get("data-src", "") if img else "")
    return {
        "id": match.group(1) if match else title,
        "title": title,
        "url": site["url"],
    }


def fetch_comicwalker(site):
    # Next.js page; the episode list is in the embedded __NEXT_DATA__ JSON.
    # updateDate is unreliable (old episodes get re-dated), so pick the
    # highest episode number.
    soup = BeautifulSoup(get(site["url"]).text, "html.parser")
    data = json.loads(soup.find("script", id="__NEXT_DATA__").string)
    episodes = []
    for query in data["props"]["pageProps"]["dehydratedState"]["queries"]:
        q = query["state"]["data"]
        if isinstance(q, dict) and "latestEpisodes" in q:
            episodes = [e for e in q["latestEpisodes"]["result"] if e.get("isActive")]
    if not episodes:
        raise ValueError("no episodes found in __NEXT_DATA__")
    latest = max(episodes, key=lambda e: e["internal"]["episodeNo"])
    work_code = site["url"].rstrip("/").rsplit("/", 1)[-1]
    return {
        "id": latest["code"],
        "title": latest["title"],
        "url": f"https://comic-walker.com/detail/{work_code}/episodes/{latest['code']}",
    }


FETCHERS = {
    "rss": fetch_rss,
    "comicnettai": fetch_comicnettai,
    "comicwalker": fetch_comicwalker,
}


# --- State and notifications ---


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def notify(topic, series, chapter):
    # JSON publishing avoids non-ASCII (Japanese) text in HTTP headers.
    requests.post(
        "https://ntfy.sh/",
        json={
            "topic": topic,
            "title": series,
            "message": chapter["title"],
            "click": chapter["url"],
        },
        timeout=TIMEOUT,
    ).raise_for_status()


# --- Main ---


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="print latest chapters; no notifications or state writes")
    parser.add_argument("--only", metavar="SITE_KEY", help="check a single site")
    args = parser.parse_args()

    sites = load_json(SITES_FILE, {})
    if args.only:
        if args.only not in sites:
            sys.exit(f"unknown site key: {args.only} (known: {', '.join(sites)})")
        sites = {args.only: sites[args.only]}

    topic = os.environ.get("NTFY_TOPIC")
    if not args.dry_run and not topic:
        sys.exit("NTFY_TOPIC is not set (use --dry-run to test without it)")

    state = load_json(STATE_FILE, {})
    failures = 0

    for i, (key, site) in enumerate(sites.items()):
        if i:
            time.sleep(DELAY_BETWEEN_SITES)
        try:
            chapter = FETCHERS[site["type"]](site)
        except Exception as e:
            failures += 1
            print(f"[{key}] ERROR: {e!r}", file=sys.stderr)
            continue

        if args.dry_run:
            print(f"[{key}] {site['name']}: {chapter['title']}\n    id={chapter['id']}\n    {chapter['url']}")
            continue

        previous = state.get(key)
        if previous is None:
            print(f"[{key}] first run, recording {chapter['title']}")
        elif previous["id"] != chapter["id"]:
            print(f"[{key}] NEW: {chapter['title']}")
            try:
                notify(topic, site["name"], chapter)
            except Exception as e:
                failures += 1
                print(f"[{key}] notify failed, will retry next run: {e!r}", file=sys.stderr)
                continue
        else:
            print(f"[{key}] no change ({chapter['title']})")
        state[key] = chapter

    if not args.dry_run:
        save_state(state)

    if failures == len(sites) and sites:
        sys.exit(1)


if __name__ == "__main__":
    main()
