# AGENTS.md

This repository builds `bookfetch`, a stdlib-only Python CLI for searching and downloading books and papers from Anna's Archive, Z-Library, Project Gutenberg, Standard Ebooks, Open Library / Internet Archive and arXiv. It was previously `annas-archive-cli`.

## Layout

- `src/bookfetch/common.py`: shared pieces: errors (`BookfetchError`, `MissingConfigError`), User-Agent, file name helpers, and `result()`, which builds the common search result shape `{source, id, title, author, publisher, year, language, extension, size, extra}`.
- `src/bookfetch/common.py` also holds `Account` (one named account of one source; `repr` and `describe()` never show secret values) and the error classes the CLI uses to decide on fallback: `LoginError` and `QuotaExhaustedError`.
- `src/bookfetch/config.py`: builds the account list per source: the `env` account from environment variables first (`env_account()` in each source module), then the accounts from the optional TOML config file in file order, then, for modules with `ANONYMOUS = True`, a built-in account named `anonymous`. Also the config path lookup, domain precedence (env, then file, then built-in; only for sources with a `DOMAINS_ENV`), and the permission warning for inline secrets.
- `src/bookfetch/net.py`: HTTP for the open sources: `Http` with an allow list of host/path `Rule`s checked on every request and every redirect hop, per-host pacing (`Pacer`, injectable clock), the `bookfetch/<version>` User-Agent, and `download()` (refuses HTML, checks magic bytes, size and MD5, writes `<name>.part` then renames).
- `src/bookfetch/feeds.py`: a small Atom / OPDS 1 reader (Gutenberg, Standard Ebooks, arXiv). It only returns data; it never fetches links.
- `src/bookfetch/sources/`: one module per source, registered in `SOURCES` in `__init__.py`. Each module defines `DEFAULT_DOMAINS`, `DOMAINS_ENV`, `ACCOUNT_FIELDS`, `SECRET_FIELDS`, `NOT_CONFIGURED`, `env_account()`, and a `Source` class with `from_account(account, config, browser=True)` (raises `MissingConfigError` when the account is unusable), `from_env()` (older single-account path), `search()`, `download(id, out, name) -> Path` (sets `downloads_left`; raises a `QuotaExhaustedError` subclass when the account has no downloads left, before spending one), `quota() -> dict`, and optionally `close()`.
  - `annas.py`: key loading, login session, HTML parsing (`parse_results`, `parse_quota`), and the member fast-download API.
  - `base.py`: `OpenSource`, the shared base for the account-less sources (`from_account` accepts only the `anonymous` account, `quota()` reports limits, `downloads_note` replaces the quota line).
  - `gutenberg.py`: OPDS search feed and per-book OPDS entry, then `/ebooks/<n>.<format>`.
  - `standardebooks.py`: OPDS search feed (`/feeds/opds/all?query=`), downloads from `/ebooks/<path>/downloads/<file>?source=download`, optional Patrons Circle email (HTTP Basic, empty password), and `DownloadLog`, a small JSON log under `$XDG_STATE_HOME/bookfetch/` that keeps anonymous downloads under the per-address limits.
  - `openlibrary.py`: Open Library `search.json` (`ebook_access=public`, `availability`), one archive.org `advancedsearch.php` call to drop audio and access-restricted items, `/metadata/<id>` and `/download/<id>/<file>` for the file.
  - `arxiv.py`: `export.arxiv.org/api/query` (title phrase first, then all significant words) and `arxiv.org/pdf/<id>`, including old-style ids like `hep-th/9901001`.
  - `zlib.py`: the Z-Library JSON API ("eapi") with two transports: `HttpTransport` (plain HTTP; login and profile only, because search sits behind a browser check) and `BrowserTransport` (runs `fetch` inside its own tab of a Chromium reached over CDP). The file itself is fetched over plain HTTP first, with the browser as fallback.
- `src/bookfetch/cdp.py`: a minimal DevTools client: `/json/list`, `/json/new`, `/json/close`, plus a small RFC 6455 WebSocket client.
- `src/bookfetch/cli.py`: argument parsing, `SOURCE:ID` routing (split on the first `:` only, so ids may contain `/` and `:`), account selection (`--account`), fallback to the next account (`run_with_fallback`: search on login errors, download on login or quota errors, each account at most once), searching several sources in parallel threads with per-source error isolation, and exit codes (0 if any source worked, 1 if all tried sources failed, 2 if none was available).
- `tests/`: offline tests. `tests/fixtures/` holds small hand-made HTML, JSON and Atom shaped like the real responses, with made-up ids and public-domain titles. `tests/conftest.py` blocks non-loopback network access and, unless a test is marked `open_sources`, removes the four open sources from `SOURCES` so the older tests see only annas and zlib.
- `skills/bookfetch/SKILL.md`: a generic agent skill describing how to use the CLI.

## Checks

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Rules

- Tests never hit the live sites, and `tests/conftest.py` clears the `ANNAS_*`, `ZLIB_*`, `STANDARDEBOOKS_EMAIL` and `BOOKFETCH_*` variables and points `XDG_CONFIG_HOME` and `XDG_STATE_HOME` at a temporary directory, so a developer's real accounts never leak into tests. Use fake openers, fake transports, or the local fake WebSocket server; every real download spends a daily quota.
- Keep runtime dependencies at zero (stdlib only). The WebSocket client is deliberately small: it only needs masked text frames out and text, continuation, ping, and close frames in.
- The browser transport opens its own tab and closes it. Never navigate, reuse, or close a user's existing tabs.
- Anna's Archive and Z-Library keep one consistent desktop browser User-Agent for plain HTTP requests. The open sources send `bookfetch/<version> (+https://github.com/soffits/bookfetch-cli)` instead: the Internet Archive requires a descriptive User-Agent for automated requests, and it is the honest choice for public APIs.
- Be polite to the open sources: keep the per-host gaps (Gutenberg 2 s, Standard Ebooks 2 s, Open Library and archive.org 1 s each, arXiv 3 s for API and PDFs together), keep the number of requests per command small (no per-result detail requests during search), never retry on 403 (arXiv uses it to block), and never add bulk or crawling features.
- Never follow links from pages or feeds. Every URL is built from a fixed pattern and must match the module's `RULES`. Standard Ebooks hides a `/honeypot` link on every page that bans the address for 24 hours.
- Gutenberg: only the OPDS feeds and the `/ebooks/<n>.<format>` files (its robot policy forbids scraping the HTML site). The XML OPDS feed is due to be retired in 2027; keep its parsing isolated in `gutenberg.py`.
- Open Library / Internet Archive: only public files. Skip access-restricted and dark items, non-`texts` items (LibriVox audio), files marked `private`, and every DRM file (`_lcp.epub`, `.lcpdf`, `_encrypted.pdf`, `.acsm`, any "Encrypted" format). No accounts for this source: logging in only adds DRM lending, which bookfetch does not touch.
- Configuration comes from environment variables and the optional TOML config file (stdlib `tomllib`). Environment variables keep working unchanged and define the first account. No hardcoded paths.
- Only fall back to another account on errors that prove no download was spent (login rejected, no downloads left). Never loop over the same account.
- Never log, print, or commit a key, password, cookies, or saved pages from a real account. Fixtures must be hand-made.
- Only official domains go in `DEFAULT_DOMAINS` (as listed on each source's Wikipedia article).
- If a site's responses change, update the parser and the synthetic fixture together.
- A new source is a new module in `sources/` with the same `Source` interface, added to `SOURCES`. Sources that work without an account set `ANONYMOUS = True` and usually subclass `OpenSource`. No plugin framework.
- Repository text is English only. Commit messages use Conventional Commits. License is MIT.
