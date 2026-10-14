# AGENTS.md

This repository builds `bookfetch`, a stdlib-only Python CLI for searching and downloading books from Anna's Archive and Z-Library. It was previously `annas-archive-cli`.

## Layout

- `src/bookfetch/common.py`: shared pieces: errors (`BookfetchError`, `MissingConfigError`), User-Agent, file name helpers, and `result()`, which builds the common search result shape `{source, id, title, author, publisher, year, language, extension, size, extra}`.
- `src/bookfetch/common.py` also holds `Account` (one named account of one source; `repr` and `describe()` never show secret values) and the error classes the CLI uses to decide on fallback: `LoginError` and `QuotaExhaustedError`.
- `src/bookfetch/config.py`: builds the account list per source: the `env` account from environment variables first (`env_account()` in each source module), then the accounts from the optional TOML config file in file order. Also the config path lookup, domain precedence (env, then file, then built-in), and the permission warning for inline secrets.
- `src/bookfetch/sources/`: one module per source, registered in `SOURCES` in `__init__.py`. Each module defines `DEFAULT_DOMAINS`, `DOMAINS_ENV`, `ACCOUNT_FIELDS`, `SECRET_FIELDS`, `NOT_CONFIGURED`, `env_account()`, and a `Source` class with `from_account(account, config, browser=True)` (raises `MissingConfigError` when the account is unusable), `from_env()` (older single-account path), `search()`, `download(id, out, name) -> Path` (sets `downloads_left`; raises a `QuotaExhaustedError` subclass when the account has no downloads left, before spending one), `quota() -> dict`, and optionally `close()`.
  - `annas.py`: key loading, login session, HTML parsing (`parse_results`, `parse_quota`), and the member fast-download API.
  - `zlib.py`: the Z-Library JSON API ("eapi") with two transports: `HttpTransport` (plain HTTP; login and profile only, because search sits behind a browser check) and `BrowserTransport` (runs `fetch` inside its own tab of a Chromium reached over CDP). The file itself is fetched over plain HTTP first, with the browser as fallback.
- `src/bookfetch/cdp.py`: a minimal DevTools client: `/json/list`, `/json/new`, `/json/close`, plus a small RFC 6455 WebSocket client.
- `src/bookfetch/cli.py`: argument parsing, `SOURCE:ID` routing, account selection (`--account`), fallback to the next account (`run_with_fallback`: search on login errors, download on login or quota errors, each account at most once), per-source error isolation, and exit codes (0 if any source worked, 1 if all configured sources failed, 2 if none was configured).
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

- Tests never hit the live sites, and `tests/conftest.py` clears the `ANNAS_*`, `ZLIB_*` and `BOOKFETCH_*` variables and points `XDG_CONFIG_HOME` at a temporary directory, so a developer's real accounts never leak into tests. Use fake openers, fake transports, or the local fake WebSocket server; every real download spends a daily quota.
- Keep runtime dependencies at zero (stdlib only). The WebSocket client is deliberately small: it only needs masked text frames out and text, continuation, ping, and close frames in.
- The browser transport opens its own tab and closes it. Never navigate, reuse, or close a user's existing tabs.
- Keep one consistent desktop browser User-Agent for plain HTTP requests.
- Configuration comes from environment variables and the optional TOML config file (stdlib `tomllib`). Environment variables keep working unchanged and define the first account. No hardcoded paths.
- Only fall back to another account on errors that prove no download was spent (login rejected, no downloads left). Never loop over the same account.
- Never log, print, or commit a key, password, cookies, or saved pages from a real account. Fixtures must be hand-made.
- Only official domains go in `DEFAULT_DOMAINS` (as listed on Wikipedia's Anna's Archive and Z-Library articles).
- If a site's responses change, update the parser and the synthetic fixture together.
- A new source is a new module in `sources/` with the same `Source` interface, added to `SOURCES`. No plugin framework.
- Repository text is English only. Commit messages use Conventional Commits. License is MIT.
