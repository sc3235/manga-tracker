# Manga Update Tracker

Python script that checks a set of Japanese manga pages on a schedule and sends a push notification to my iPhone (via ntfy) when a new chapter appears. Runs on GitHub Actions; no server, no native app.

## How it works

1. `tracker.py` loads `sites.json` (what to check) and `state.json` (last seen chapter per site).
2. For each site, a site-specific fetcher returns the latest chapter as `{"id": ..., "title": ..., "url": ...}`.
3. If `id` differs from `state.json`, send an ntfy notification and update state.
4. The GitHub Actions workflow commits `state.json` back to the repo if it changed.

## Stack

- Python 3.12, `requests`, `beautifulsoup4`, `feedparser`
- Playwright only if manga-one requires it (avoid if an API/JSON route works)
- Notifications: `POST https://ntfy.sh/<topic>`; topic comes from env var `NTFY_TOPIC` (GitHub secret). Never hardcode it.

## Sites

| Series | Site | Strategy | URL to watch |
|---|---|---|---|
| ひらやすみ | bigcomics.jp | RSS | https://bigcomics.jp/series/8082b80580bd3/rss |
| ホストと社畜 | comic-action.com | RSS | https://comic-action.com/rss/series/2550689798598882943 |
| 煙たい話 | comicnettai.com | HTML scrape; newest chapter listed first (e.g. "第49話 … 2026.09.04") | https://www.comicnettai.com/book/9 |
| 天幕のジャードゥーガル | souffle.life | HTML scrape of series page (NOT a chapter page). Chapter URLs look like `/manga/tenmaku-no-ja-dougal/tenmaku045-20260925/`. Updates on the 25th monthly. Also check if WordPress exposes a feed. | https://souffle.life/author/tenmaku-no-ja-dougal/ |
| 光が死んだ夏 | comic-walker.com | HTML scrape; episode list is server-rendered, newest first (e.g. "第49話-3", 2026/09/01). Episode IDs like `KC_0015710005000031_E`. | https://comic-walker.com/detail/KC_001571_S |
| アフターゴッド | manga-one.com | HARD: Next.js, chapter list is not in plain HTML. Investigate in order: embedded JSON in page source → internal API calls (browser dev tools network tab) → Playwright as last resort. | https://manga-one.com/manga/1755 |

Last confirmed chapter on manga-one: 第101話 (chapter id 353440).

## Findings (2026-09-28)

- souffle: WordPress author feed `https://souffle.life/author/tenmaku-no-ja-dougal/feed/` works and lists only chapters, so it uses the `rss` type (no `souffle` scraper needed).
- comicnettai: viewer links carry an encrypted per-request `cid`, so change detection uses the content ID from the thumbnail path (`book_contents/<id>/`) and the notification links to the series page.
- comic-walker: episode list is in `__NEXT_DATA__` (`latestEpisodes`). `updateDate` is unreliable (old episodes get re-dated), so the latest is picked by `internal.episodeNo`.
- ntfy: published as JSON to `https://ntfy.sh/` so Japanese titles aren't sent in HTTP headers.

## Conventions

- One fetcher function per site type, registered by a `type` field in `sites.json` (`rss`, `comicnettai`, `souffle`, `comicwalker`, `mangaone`). Adding a site should mean editing `sites.json`, not core logic.
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

- `.github/workflows/check.yml` ("Check chapters"), cron daily at 11:00 UTC = 7am New York (6am in winter), plus `workflow_dispatch`.
- `permissions: contents: write`; commit `state.json` only when it changed.
- `NTFY_TOPIC` from repository secrets.
- Known risks: scheduled runs can be delayed; GitHub disables schedules after 60 days of no repo activity (state commits usually prevent this); some sites may block GitHub runner IPs. If a site blocks, note it in this file.

## Build order

1. `tracker.py` skeleton + RSS fetcher + `--dry-run`, verify the two RSS sites.
2. comicnettai, souffle, comicwalker scrapers.
3. ntfy notifications + state handling.
4. GitHub Actions workflow.
5. manga-one (investigate last).
