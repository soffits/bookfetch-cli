# AGENTS.md

This repository builds `annas`, a stdlib-only Python CLI for searching Anna's Archive and downloading files through the member API.

## Layout

- `src/annas_archive_cli/client.py`: key loading, domain list, login session, HTML parsing (`parse_results`, `parse_quota`), file name handling, and the fast-download API.
- `src/annas_archive_cli/cli.py`: argument parsing, output formatting, and exit codes (0 ok, 1 site or API error, 2 missing key).
- `tests/`: offline tests. `tests/fixtures/` holds small synthetic HTML pages shaped like the real ones, with made-up MD5s and public-domain titles.
- `skills/annas-archive/SKILL.md`: a generic agent skill describing how to use the CLI.

## Checks

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

## Rules

- Tests never hit the live site. Mock the opener instead; every real download call spends a member's daily quota.
- Keep runtime dependencies at zero (stdlib only).
- Keep one consistent desktop browser User-Agent for all requests.
- Never log, print, or commit a secret key, cookies, or saved pages from a real account. Fixtures must be hand-made.
- Only official domains go in `DEFAULT_DOMAINS` (as listed on Wikipedia's Anna's Archive article).
- If the site's HTML changes, update the parser and the synthetic fixture together.
- Repository text is English only. Commit messages use Conventional Commits. License is MIT.
