"""Check manga sites for new chapters and notify via ntfy."""

import argparse
import calendar
import json
import os
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
SITES_FILE = ROOT / "sites.json"
STATE_FILE = ROOT / "state.json"
README_FILE = ROOT / "README.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}
TIMEOUT = 20
DELAY_BETWEEN_SITES = 2
JST = ZoneInfo("Asia/Tokyo")
DISPLAY_TZ = ZoneInfo("America/New_York")  # for "Last checked" in the README
HISTORY_FOR_ESTIMATE = 8  # recent release dates used to estimate the next one


def get(url):
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def to_jst_date(dt):
    """ISO date (JST) for an aware datetime."""
    return dt.astimezone(JST).date().isoformat()


# --- Fetchers ---
# Each returns (chapter, history): chapter is {"id", "title", "url", "date"}
# for the latest chapter; history is recent release dates (ISO), newest first.


def fetch_rss(site):
    feed = feedparser.parse(get(site["url"]).content)
    if not feed.entries:
        raise ValueError("feed has no entries")
    entries = feed.entries
    dated = all(e.get("published_parsed") for e in entries)
    if dated:
        entries = sorted(entries, key=lambda e: e.published_parsed, reverse=True)
    dates = [
        to_jst_date(datetime(*e.published_parsed[:6], tzinfo=timezone.utc))
        for e in entries
    ] if dated else []
    latest = entries[0]
    chapter = {
        "id": latest.get("id") or latest.link,
        "title": latest.title,
        "url": latest.link.split("?")[0],
        "date": dates[0] if dates else None,
    }
    return chapter, dates


def fetch_comicnettai(site):
    # Chapters are listed newest first. The viewer link carries an encrypted,
    # per-request token, so use the content ID from the thumbnail path instead
    # and link the notification to the series page.
    soup = BeautifulSoup(get(site["url"]).text, "html.parser")
    items = soup.select("a.detail--product__item")
    if not items:
        raise ValueError("no chapter items found")
    dates = []
    for item in items:
        sdate = item.select_one(".detail--product__item__sdate")
        if sdate:
            dates.append(sdate.get_text(strip=True).replace(".", "-"))  # 2026.09.04
    item = items[0]
    title = item.select_one(".detail--product__item__title").get_text(strip=True)
    img = item.select_one("img")
    match = re.search(r"/book_contents/(\d+)/", img.get("data-src", "") if img else "")
    chapter = {
        "id": match.group(1) if match else title,
        "title": title,
        "url": site["url"],
        "date": dates[0] if dates else None,
    }
    return chapter, dates


def fetch_comicwalker(site):
    # Next.js page; the episode list is in the embedded __NEXT_DATA__ JSON.
    # updateDate is unreliable for old episodes (they get re-dated), so order
    # by episode number and only use the newest ones' dates.
    soup = BeautifulSoup(get(site["url"]).text, "html.parser")
    data = json.loads(soup.find("script", id="__NEXT_DATA__").string)
    episodes = []
    for query in data["props"]["pageProps"]["dehydratedState"]["queries"]:
        q = query["state"]["data"]
        if isinstance(q, dict) and "latestEpisodes" in q:
            episodes = [e for e in q["latestEpisodes"]["result"] if e.get("isActive")]
    if not episodes:
        raise ValueError("no episodes found in __NEXT_DATA__")
    episodes.sort(key=lambda e: e["internal"]["episodeNo"], reverse=True)
    dates = [
        to_jst_date(datetime.fromisoformat(e["updateDate"].replace("Z", "+00:00")))
        for e in episodes[:HISTORY_FOR_ESTIMATE]
    ]
    latest = episodes[0]
    work_code = site["url"].rstrip("/").rsplit("/", 1)[-1]
    chapter = {
        "id": latest["code"],
        "title": latest["title"],
        "url": f"https://comic-walker.com/detail/{work_code}/episodes/{latest['code']}",
        "date": dates[0],
    }
    return chapter, dates


def decode_protobuf(data):
    """Minimal protobuf wire-format decoder: {field_number: [values]}.

    Varints become ints; length-delimited fields stay bytes (decode nested
    messages by calling this again).
    """
    def varint(i):
        value = shift = 0
        while True:
            byte = data[i]
            i += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return value, i

    fields, i = {}, 0
    while i < len(data):
        key, i = varint(i)
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, i = varint(i)
        elif wire_type == 2:
            length, i = varint(i)
            value, i = data[i:i + length], i + length
        elif wire_type == 1:
            value, i = data[i:i + 8], i + 8
        elif wire_type == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        fields.setdefault(number, []).append(value)
    return fields


def fetch_mangaone(site):
    # The series page is a client-rendered Next.js app (and 404s without JS).
    # Its own API returns the chapter list as protobuf, newest first:
    #   response.1 = list; list.1 = chapters
    #   chapter: 1 = id, 2 = number ("第109話"), 3 = subtitle, 5 = date ("2026/09/09")
    title_id = re.search(r"/manga/(\d+)", site["url"]).group(1)
    resp = get(
        "https://manga-one.com/api/client?rq=viewer/chapter_list"
        f"&title_id={title_id}&type=chapter&page=1&limit={HISTORY_FOR_ESTIMATE}&sort_type=desc"
    )
    chapter_list = decode_protobuf(decode_protobuf(resp.content)[1][0])
    chapters = [decode_protobuf(c) for c in chapter_list.get(1, [])]
    if not chapters:
        raise ValueError("empty chapter list")

    def text(fields, number):
        return fields.get(number, [b""])[0].decode("utf-8")

    dates = [text(c, 5).replace("/", "-") for c in chapters if text(c, 5)]
    latest = chapters[0]
    chapter_id = latest[1][0]
    return {
        "id": str(chapter_id),
        "title": f"{text(latest, 2)} {text(latest, 3)}".strip(),
        "url": f"https://manga-one.com/manga/{title_id}/chapter/{chapter_id}",
        "date": dates[0] if dates else None,
    }, dates


FETCHERS = {
    "rss": fetch_rss,
    "comicnettai": fetch_comicnettai,
    "comicwalker": fetch_comicwalker,
    "mangaone": fetch_mangaone,
}


# --- Next-release estimate ---

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def next_month(d):
    return (d.year + d.month // 12, d.month % 12 + 1)


def nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def estimate_next(site, history):
    """Return (ISO date, is_estimate) for the next release, or (None, False).

    sites.json can set a fixed "schedule":
      {"day": 25}                   -> the 25th of each month
      {"weekday": "fri", "nth": 1}  -> first Friday of each month
    Otherwise: latest date + median gap between recent releases.
    """
    if not history:
        return None, False
    last = date.fromisoformat(history[0])
    schedule = site.get("schedule")

    if schedule:
        year, month = last.year, last.month
        for _ in range(3):
            if "day" in schedule:
                day = min(schedule["day"], calendar.monthrange(year, month)[1])
                candidate = date(year, month, day)
            else:
                weekday = WEEKDAYS.index(schedule["weekday"])
                candidate = nth_weekday(year, month, weekday, schedule.get("nth", 1))
            if candidate > last:
                return candidate.isoformat(), False
            year, month = next_month(date(year, month, 1))
        return None, False

    dates = sorted({date.fromisoformat(d) for d in history[:HISTORY_FOR_ESTIMATE]}, reverse=True)
    gaps = [(a - b).days for a, b in zip(dates, dates[1:])]
    if len(gaps) < 2:
        return None, False
    return (last + timedelta(days=round(statistics.median(gaps)))).isoformat(), True


# --- State, README and notifications ---


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def md_escape(text):
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def short_date(iso):
    """2026-09-05 -> 9/5/26 (keeps the README table narrow on small screens)."""
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}/{d.year % 100:02d}"


def write_readme(sites, state, failed):
    """Rewrite the status table between the markers in README.md."""
    today = datetime.now(JST).date().isoformat()
    rows = ["| Series | Last | Released | Next |", "|---|---|---|---|"]

    def sort_key(key):
        # Upcoming by date, then unknown, then overdue at the bottom.
        nxt = (state.get(key) or {}).get("next")
        if nxt is None:
            return (1, "")
        return (2 if nxt < today else 0, nxt)

    for key in sorted(sites, key=sort_key):
        site = sites[key]
        name = md_escape(site["name"])
        if key in failed:
            name += " ⚠️"
        chapter = state.get(key)
        if chapter is None:
            rows.append(f"| {name} | — | — | — |")
            continue
        title = chapter["title"].replace(site["name"], "").strip() or chapter["title"]
        latest = f"[{md_escape(title)}]({chapter['url']})"
        released = short_date(chapter["date"]) if chapter.get("date") else "—"
        nxt = "—"
        if chapter.get("next"):
            nxt = ("~" if chapter.get("next_is_estimate") else "") + short_date(chapter["next"])
            if chapter["next"] < today:
                nxt += "\u00a0⏰"  # non-breaking space keeps it on one line
        rows.append(f"| {name} | {latest} | {released} | {nxt} |")

    checked = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M %Z")
    notes = [
        "",
        f"_Last checked: {checked}. Dates are JST. "
        "`~` = estimated from recent release gaps; ⏰ = overdue; ⚠️ = check failed this run._",
    ]
    table = "\n".join(rows + notes)

    start, end = "<!-- status:start -->", "<!-- status:end -->"
    text = README_FILE.read_text(encoding="utf-8") if README_FILE.exists() else f"{start}\n{end}\n"
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    README_FILE.write_text(f"{before}{start}\n{table}\n{end}{after}", encoding="utf-8")


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
                        help="print latest chapters; no notifications or file writes")
    parser.add_argument("--only", metavar="SITE_KEY", help="check a single site")
    args = parser.parse_args()

    all_sites = load_json(SITES_FILE, {})
    sites = all_sites
    if args.only:
        if args.only not in all_sites:
            sys.exit(f"unknown site key: {args.only} (known: {', '.join(all_sites)})")
        sites = {args.only: all_sites[args.only]}

    topic = os.environ.get("NTFY_TOPIC")
    if not args.dry_run and not topic:
        sys.exit("NTFY_TOPIC is not set (use --dry-run to test without it)")

    state = load_json(STATE_FILE, {})
    failed = set()

    for i, (key, site) in enumerate(sites.items()):
        if i:
            time.sleep(DELAY_BETWEEN_SITES)
        try:
            chapter, history = FETCHERS[site["type"]](site)
        except Exception as e:
            failed.add(key)
            print(f"[{key}] ERROR: {e!r}", file=sys.stderr)
            continue
        chapter["next"], chapter["next_is_estimate"] = estimate_next(site, history)

        if args.dry_run:
            nxt = ("~" if chapter["next_is_estimate"] else "") + str(chapter["next"])
            print(f"[{key}] {site['name']}: {chapter['title']}  (released {chapter['date']}, next {nxt})"
                  f"\n    id={chapter['id']}\n    {chapter['url']}")
            continue

        previous = state.get(key)
        if previous is None:
            print(f"[{key}] first run, recording {chapter['title']}")
        elif previous["id"] != chapter["id"]:
            print(f"[{key}] NEW: {chapter['title']}")
            try:
                notify(topic, site["name"], chapter)
            except Exception as e:
                failed.add(key)
                print(f"[{key}] notify failed, will retry next run: {e!r}", file=sys.stderr)
                continue
        else:
            print(f"[{key}] no change ({chapter['title']})")
        state[key] = chapter

    if not args.dry_run:
        save_state(state)
        write_readme(all_sites, state, failed)

    if sites and len(failed) == len(sites):
        sys.exit(1)


if __name__ == "__main__":
    main()
