# Manga Update Tracker

Python script that checks a set of Japanese manga pages on a schedule and sends a push notification to my iPhone (via ntfy) when a new chapter appears. Runs on GitHub Actions; no server, no native app.

## How it works

1. `tracker.py` loads `sites.json` (what to check) and `state.json` (last seen chapter per site).
2. For each site, a site-specific fetcher returns the latest chapter as `{"id": ..., "title": ..., "url": ...}`.
3. If `id` differs from `state.json`, send an ntfy notification and update state.
4. The GitHub Actions workflow commits `state.json` back to the repo if it changed.

## Stack

- Python 3.12, `requests`, `beautifulsoup4`, `feedparser`
- No Playwright: manga-one works through its internal API (see Findings)
- Notifications: `POST https://ntfy.sh/<topic>`; topic comes from env var `NTFY_TOPIC` (GitHub secret). Never hardcode it.

## Sites

| Series | Site | Strategy | URL to watch |
|---|---|---|---|
| ひらやすみ | bigcomics.jp | RSS | https://bigcomics.jp/series/8082b80580bd3/rss |
| ホストと社畜 | comic-action.com | RSS + episode page for next date (`comicaction`) | https://comic-action.com/rss/series/2550689798598882943 |
| 煙たい話 | comicnettai.com | HTML scrape; newest chapter listed first (e.g. "第49話 … 2026.09.04") | https://www.comicnettai.com/book/9 |
| 天幕のジャードゥーガル | souffle.life | RSS (WordPress author feed). Updates on the 25th monthly. | https://souffle.life/author/tenmaku-no-ja-dougal/feed/ |
| ボールアンドチェイン | shuro.world | HTML scrape (WordPress); page lists only the newest episode plus 第1–2話. No known schedule, assumed monthly. | https://shuro.world/manga/ballandchain/ |

## Findings (2026-09-28)

- souffle: WordPress author feed `https://souffle.life/author/tenmaku-no-ja-dougal/feed/` works and lists only chapters, so it uses the `rss` type (no `souffle` scraper needed).
- comicnettai: viewer links carry an encrypted per-request `cid`, so change detection uses the content ID from the thumbnail path (`book_contents/<id>/`) and the notification links to the series page.
- Status table: every normal run rewrites the table between `<!-- status:start/end -->` in `README.md` (series, latest chapter, release date in JST, next release). Next release comes from an optional `schedule` in `sites.json` (`{"day": 25}`, `{"weekday": "fri", "nth": 1}`, or announced one-off dates `{"dates": ["2026-10-13", "2026-10-20"]}`, which fall back to the estimate once all have passed), otherwise it's estimated as latest + median gap of recent releases (shown with `~`). A site can also set `"final": "YYYY-MM-DD"` (the last chapter's date): once a chapter released on or after it is seen, the series moves to a bottom **completed** section (✅) as a reminder to delete it from `sites.json`. A series is only marked overdue (🔴, listed below an **overdue** divider row) once the next date is more than 7 days past (`OVERDUE_GRACE_DAYS`), fixed schedules included: 煙たい話 is usually the 1st Friday but sometimes slips to the 2nd (holidays) or skips a month.
- manga-one: the series page 404s without JS (client-rendered). The site's own API `https://manga-one.com/api/client?rq=viewer/chapter_list&title_id=1755&type=chapter&page=1&limit=8&sort_type=desc` works without login and returns protobuf, newest first; `decode_protobuf` in `tracker.py` reads it (response.1 = list, list.1 = chapters; chapter fields 1 id, 2 number, 3 subtitle, 5 date `YYYY/MM/DD`). Endpoint names come from the site's JS bundles (search `path:"` in `/_next/static/chunks/*.js`) if it ever changes.
- shuro (2026-10-11): `/manga/<slug>/feed/` is a comments feed and `/episode/feed/` mixes all series with no series marker, so the series page is scraped. Newest episode = highest post ID in `/episode/<id>/` links; date = the page's "最終更新". With only one date there's no gap history, so it uses schedule `{"monthly": true}` (latest + 1 month, shown as an estimate 🟡).
- comic-action (2026-10-13): the latest episode page says when it goes free (`2026年10月20日に無料公開予定`), which matches the next chapter's release (RSS `giga:freeTermStartDate` of each chapter = next chapter's `pubDate`). The `comicaction` type reads it as a fixed next date (🟢); if the page fetch fails or the text is missing, it falls back to the gap estimate.
- ntfy: published as JSON to `https://ntfy.sh/` so Japanese titles aren't sent in HTTP headers.

## Conventions

- One fetcher function per site type, registered by a `type` field in `sites.json` (`rss`, `comicaction`, `comicnettai`, `mangaone`, `shuro`). Adding a site should mean editing `sites.json`, not core logic.
- Prefer stable identifiers (episode ID / URL) over display text for change detection.
- Send a browser-like `User-Agent`, 20s timeouts, and a short delay between sites.
- One site failing must not stop the others. Log the error and continue; do not overwrite that site's state on failure.
- First run for a new site: record state silently, do not notify.
- Notification format: title = series name, body = chapter title, `Click` header = chapter URL.
- Keep code simple and readable; no framework, no database.

## CLI

- `python tracker.py` normal run
- `python tracker.py --dry-run` fetch and print latest chapters, no notifications, no state writes
- `python tracker.py --only <site-key>` check one site (for debugging scrapers)

## GitHub Actions

- `.github/workflows/check.yml` ("Check chapters"), cron twice daily at 05:23/19:23 UTC ≈ 1:23am/3:23pm New York (12:23am/2:23pm in winter). Scheduled early on purpose: in late Sep/early Oct 2026, GitHub started runs ~4.5–6.5h late in the morning slot and ~2.5–3.5h late in the evening slot, so actual runs land around 6am/6pm. Minute 23 avoids top-of-hour congestion but did not reduce the delay., plus `workflow_dispatch`.
- `permissions: contents: write`; commit `state.json` and `README.md` only when they changed.
- `NTFY_TOPIC` from repository secrets.
- Known risks: scheduled runs can be delayed; GitHub disables schedules after 60 days of no repo activity (state commits usually prevent this); some sites may block GitHub runner IPs. If a site blocks, note it in this file.

## Build order

1. `tracker.py` skeleton + RSS fetcher + `--dry-run`, verify the two RSS sites.
2. comicnettai, souffle, comicwalker scrapers (comic-walker / 光が死んだ夏 removed 2026-10-13; tracked in its app instead).
3. ntfy notifications + state handling.
4. GitHub Actions workflow.
5. manga-one (investigate last).

All steps done as of 2026-09-28.
