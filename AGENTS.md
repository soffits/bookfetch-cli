# AGENTS.md

This repository builds `bookfetch`, a stdlib-only Python CLI for searching and downloading books from Anna's Archive and Z-Library. It was previously `annas-archive-cli`.

## Layout

- `src/bookfetch/common.py`: shared pieces: errors (`BookfetchError`, `MissingConfigError`), User-Agent, file name helpers, and `result()`, which builds the common search result shape `{source, id, title, author, publisher, year, language, extension, size, extra}`.
- `src/bookfetch/sources/`: one module per source, registered in `SOURCES` in `__init__.py`. Each has a `Source` class with `from_env()` (raises `MissingConfigError` when unconfigured), `search()`, `download(id, out, name) -> Path` (sets `downloads_left`), `quota() -> dict`, and optionally `close()`.
  - `annas.py`: key loading, login session, HTML parsing (`parse_results`, `parse_quota`), and the member fast-download API.
  - `zlib.py`: the Z-Library JSON API ("eapi") with two transports: `HttpTransport` (plain HTTP; login and profile only, because search sits behind a browser check) and `BrowserTransport` (runs `fetch` inside its own tab of a Chromium reached over CDP). The file itself is fetched over plain HTTP first, with the browser as fallback.
- `src/bookfetch/cdp.py`: a minimal DevTools client: `/json/list`, `/json/new`, `/json/close`, plus a small RFC 6455 WebSocket client.
- `src/bookfetch/cli.py`: argument parsing, `SOURCE:ID` routing, per-source error isolation, and exit codes (0 if any source worked, 1 if all configured sources failed, 2 if none was configured).
- `tests/`: offline tests. `tests/fixtures/` holds small hand-made HTML and JSON shaped like the real responses, with made-up ids and public-domain titles.
- `skills/bookfetch/SKILL.md`: a generic agent skill describing how to use the CLI.

## Checks

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Rules

- Tests never hit the live sites. Use fake openers, fake transports, or the local fake WebSocket server; every real download spends a daily quota.
- Keep runtime dependencies at zero (stdlib only). The WebSocket client is deliberately small: it only needs masked text frames out and text, continuation, ping, and close frames in.
- The browser transport opens its own tab and closes it. Never navigate, reuse, or close a user's existing tabs.
- Keep one consistent desktop browser User-Agent for plain HTTP requests.
- Configuration comes from environment variables only. No hardcoded paths.
- Never log, print, or commit a key, password, cookies, or saved pages from a real account. Fixtures must be hand-made.
- Only official domains go in `DEFAULT_DOMAINS` (as listed on Wikipedia's Anna's Archive and Z-Library articles).
- If a site's responses change, update the parser and the synthetic fixture together.
- A new source is a new module in `sources/` with the same `Source` interface, added to `SOURCES`. No plugin framework.
- Repository text is English only. Commit messages use Conventional Commits. License is MIT.
