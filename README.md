# bookfetch-cli

Search and download books from [Anna's Archive](https://en.wikipedia.org/wiki/Anna%27s_Archive) and [Z-Library](https://en.wikipedia.org/wiki/Z-Library) from the command line. It has no dependencies beyond the Python standard library.

This project was previously called `annas-archive-cli` and installed an `annas` command. The command is now `bookfetch`, and `annas download MD5` becomes `bookfetch download annas:MD5`.

## What you need

Each source needs its own account. Sources that are not configured are skipped.

**Anna's Archive** needs an active membership and its secret key (shown on your account page). Downloads go through the member fast-download API, and search pages only let members through when requests come from a script.

**Z-Library** needs an account (a free one allows 10 downloads a day). Logging in and checking your quota work directly, but search and file lookups are behind a browser check that only a real browser passes. For those, bookfetch runs the requests inside a Chromium (or Chrome) that you start with remote debugging:

```bash
chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.config/bookfetch-chromium"
export BOOKFETCH_CDP_URL=http://127.0.0.1:9222
```

Open Z-Library once in that browser so it passes the check (logging in there is optional; bookfetch logs in with your account if needed). bookfetch opens its own tab, uses it, and closes it again. The debugging port gives full control of that browser to anything on your machine that can reach it, so keep it on `127.0.0.1` and use a separate browser profile for it.

## Install

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/soffits/bookfetch-cli
```

Or with pipx:

```bash
pipx install git+https://github.com/soffits/bookfetch-cli
```

Python 3.11 or newer is required.

## Usage

```bash
bookfetch search "pride and prejudice austen" --ext epub --lang en --limit 5
bookfetch search "moby dick" --source zlib --json
bookfetch download annas:0123456789abcdef0123456789abcdef --out ~/Books
bookfetch download zlib:1234567/a1b2c3 --out ~/Books --name pride-and-prejudice.epub
bookfetch quota
bookfetch accounts
```

`search` asks every configured source (or only the one given with `--source annas` or `--source zlib`) and prints each result's title, author, publisher, a summary line (language, format, size, year) and an id such as `annas:MD5` or `zlib:ID/HASH`. Pass that id to `download` exactly as printed. `--limit` applies per source. If one source fails, its error goes to standard error and the other results are still shown. `--json` prints all results in one list with the same fields for every source.

`download` saves the file and prints its path and how many downloads that source has left today. Every download counts against your daily allowance, even if the transfer fails afterwards, so pick the file before you download it.

`quota` shows recent download usage for every configured account, one line each, labelled like `annas/main`. An account that fails shows its error on its own line and the others are still listed.

`accounts` (or `config`) lists the configured accounts for each source, where each one comes from, and the domains in use. It never prints keys or passwords.

Exit codes: 0 when at least one source worked, 1 when every configured source failed, 2 when no source was configured.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `ANNAS_KEY` | Anna's Archive member secret key. |
| `ANNAS_KEY_FILE` | Path to a file containing the key. Used when `ANNAS_KEY` is not set. `--annas-key-file` overrides both. |
| `ANNAS_DOMAINS` | Comma-separated Anna's Archive domains to try, in order. Overrides the built-in list. |
| `ZLIB_EMAIL` | Z-Library account email. |
| `ZLIB_PASSWORD` | Z-Library password. |
| `ZLIB_PASSWORD_FILE` | Path to a file containing the password. Used when `ZLIB_PASSWORD` is not set. |
| `ZLIB_DOMAINS` | Z-Library domain to use (the first entry). Default `z-lib.sk`. |
| `BOOKFETCH_CONFIG` | Path to the config file (see [Several accounts](#several-accounts)). `--config` overrides it. |
| `BOOKFETCH_CDP_URL` | DevTools address of a running Chromium, for example `http://127.0.0.1:9222`. Needed for Z-Library search and file lookups. |

Prefer the `_FILE` variables so secrets don't end up in your shell history.

## Several accounts

If you have more than one account for a source, for example because several people share a machine, list them in a TOML config file. bookfetch reads it from `--config PATH`, else from `BOOKFETCH_CONFIG`, else from `$XDG_CONFIG_HOME/bookfetch/config.toml` (usually `~/.config/bookfetch/config.toml`). The file is optional.

```toml
[annas.accounts.main]
key_file = "~/.secrets/annas-key"     # or: key = "..."

[annas.accounts.spare]
key_file = "~/.secrets/annas-key-2"

[zlib.accounts.me]
email = "me@example.com"
password_file = "~/.secrets/zlib-password"   # or: password = "..."

[zlib.accounts.partner]
email = "partner@example.com"
password_file = "~/.secrets/zlib-password-2"

# Optional, same as ANNAS_DOMAINS / ZLIB_DOMAINS (the environment variables win).
[annas]
domains = ["annas-archive.pk"]
```

Accounts are used in the order they appear. If the environment variables above are set, they define one more account per source, called `env`, which comes first; with only environment variables, nothing changes. `~` in paths is expanded. Prefer `key_file` and `password_file`; if you put a key or password in the file itself, keep it private (`chmod 600`), and bookfetch warns when it isn't.

Pick an account with `--account NAME`, for example `bookfetch download annas:MD5 --account spare`. Without it:

- `search` uses the first account of each source. If it cannot log in (or its secret file can't be read), bookfetch says so and tries the next one.
- `download` uses the first account. If that account has no downloads left today, or cannot log in, bookfetch says so, moves to the next one, and prints which account was used. Each account is tried at most once. Other errors stop there, since the download may already have counted.
- `quota` lists every account.

With `--account`, only that account is used and there is no fallback. Each account must be your own or used with its owner's permission, and must follow the site's terms.

## Use only official domains

Both sites have many fake copies, and some exist to collect logins and keys. The built-in domains are the official ones. If they stop working, check the current list on Wikipedia ([Anna's Archive](https://en.wikipedia.org/wiki/Anna%27s_Archive), [Z-Library](https://en.wikipedia.org/wiki/Z-Library)) before setting `ANNAS_DOMAINS` or `ZLIB_DOMAINS`, and never enter your key or password on a site that is not listed there.

## Copyright

Copyright law differs from country to country. You are responsible for making sure that what you download is legal where you live.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests run offline against small hand-made fixtures and never contact the real sites.

## License

MIT
