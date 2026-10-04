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
| ホストと社畜 | comic-action.com | RSS | https://comic-action.com/rss/series/2550689798598882943 |
| 煙たい話 | comicnettai.com | HTML scrape; newest chapter listed first (e.g. "第49話 … 2026.09.04") | https://www.comicnettai.com/book/9 |
| 天幕のジャードゥーガル | souffle.life | RSS (WordPress author feed). Updates on the 25th monthly. | https://souffle.life/author/tenmaku-no-ja-dougal/feed/ |
| 光が死んだ夏 | comic-walker.com | HTML scrape; episode list is server-rendered, newest first (e.g. "第49話-3", 2026/09/01). Episode IDs like `KC_0015710005000031_E`. | https://comic-walker.com/detail/KC_001571_S |
| アフターゴッド | manga-one.com | Internal protobuf API (see Findings). | https://manga-one.com/manga/1755 |

## Findings (2026-09-28)

- souffle: WordPress author feed `https://souffle.life/author/tenmaku-no-ja-dougal/feed/` works and lists only chapters, so it uses the `rss` type (no `souffle` scraper needed).
- comicnettai: viewer links carry an encrypted per-request `cid`, so change detection uses the content ID from the thumbnail path (`book_contents/<id>/`) and the notification links to the series page.
- comic-walker: episode list is in `__NEXT_DATA__` (`latestEpisodes`). `updateDate` is unreliable (old episodes get re-dated), so the latest is picked by `internal.episodeNo`.
- Status table: every normal run rewrites the table between `<!-- status:start/end -->` in `README.md` (series, latest chapter, release date in JST, next release). Next release comes from an optional `schedule` in `sites.json` (`{"day": 25}` or `{"weekday": "fri", "nth": 1}`), otherwise it's estimated as latest + median gap of recent releases (shown with `~`).
- manga-one: the series page 404s without JS (client-rendered). The site's own API `https://manga-one.com/api/client?rq=viewer/chapter_list&title_id=1755&type=chapter&page=1&limit=8&sort_type=desc` works without login and returns protobuf, newest first; `decode_protobuf` in `tracker.py` reads it (response.1 = list, list.1 = chapters; chapter fields 1 id, 2 number, 3 subtitle, 5 date `YYYY/MM/DD`). Endpoint names come from the site's JS bundles (search `path:"` in `/_next/static/chunks/*.js`) if it ever changes.
- ntfy: published as JSON to `https://ntfy.sh/` so Japanese titles aren't sent in HTTP headers.

## Conventions

- One fetcher function per site type, registered by a `type` field in `sites.json` (`rss`, `comicnettai`, `comicwalker`, `mangaone`). Adding a site should mean editing `sites.json`, not core logic.
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
2. comicnettai, souffle, comicwalker scrapers.
3. ntfy notifications + state handling.
4. GitHub Actions workflow.
5. manga-one (investigate last).

All steps done as of 2026-09-28.
