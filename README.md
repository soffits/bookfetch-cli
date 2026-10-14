# bookfetch-cli

Search and download books and papers from the command line. It has no dependencies beyond the Python standard library. Sources:

| Source | `--source` | What it has | Account |
| --- | --- | --- | --- |
| [Anna's Archive](https://en.wikipedia.org/wiki/Anna%27s_Archive) | `annas` | Very large catalog of books and papers | Membership needed |
| [Z-Library](https://en.wikipedia.org/wiki/Z-Library) | `zlib` | Large catalog of books | Account needed (free tier: 10 downloads a day) |
| [Project Gutenberg](https://en.wikipedia.org/wiki/Project_Gutenberg) | `gutenberg` | More than 70,000 public-domain books | None |
| [Standard Ebooks](https://en.wikipedia.org/wiki/Standard_Ebooks) | `standardebooks` | Carefully produced editions of public-domain books | None; Patrons Circle email optional |
| [Open Library](https://en.wikipedia.org/wiki/Open_Library) / [Internet Archive](https://en.wikipedia.org/wiki/Internet_Archive) | `openlibrary` | Public scans of older books | None |
| [arXiv](https://en.wikipedia.org/wiki/ArXiv) | `arxiv` | Research papers (PDF) | None |

This project was previously called `annas-archive-cli` and installed an `annas` command. The command is now `bookfetch`, and `annas download MD5` becomes `bookfetch download annas:MD5`.

## What you need

Project Gutenberg, Standard Ebooks, Open Library and arXiv work without any setup. Anna's Archive and Z-Library each need an account; when one isn't configured, it is skipped.

**Anna's Archive** needs an active membership and its secret key (shown on your account page). Downloads go through the member fast-download API, and search pages only let members through when requests come from a script.

**Z-Library** needs an account (a free one allows 10 downloads a day). Logging in and checking your quota work directly, but search and file lookups are behind a browser check that only a real browser passes. For those, bookfetch runs the requests inside a Chromium (or Chrome) that you start with remote debugging:

```bash
chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.config/bookfetch-chromium"
export BOOKFETCH_CDP_URL=http://127.0.0.1:9222
```

Open Z-Library once in that browser so it passes the check (logging in there is optional; bookfetch logs in with your account if needed). bookfetch opens its own tab, uses it, and closes it again. The debugging port gives full control of that browser to anything on your machine that can reach it, so keep it on `127.0.0.1` and use a separate browser profile for it.

## The open sources

These four are free, public services run by small organisations, so bookfetch is deliberately gentle with them: it waits between requests, only asks for the specific addresses it needs, and identifies itself with a `bookfetch/<version>` User-Agent that links to this project. Please keep it that way: search for what you want and download single books, don't use bookfetch to mirror a whole catalog.

**Project Gutenberg** (gutenberg.org). Search uses Gutenberg's official OPDS catalog feed; a download asks the feed for that one book's details and fetches the file. bookfetch waits 2 seconds between requests. The default format is EPUB 3 with images; `--ext` also accepts `epub-noimages` (much smaller), `azw3`, `mobi`, `txt` and `html` (a zip). Gutenberg has almost no PDFs, so `--ext pdf` finds nothing. Gutenberg plans to retire this feed in 2027; bookfetch will need an update then. Ids look like `gutenberg:1342` or `gutenberg:1342:txt`.

**Standard Ebooks** (standardebooks.org). Search uses the site's OPDS search feed; downloads come from the book's download address. Without an account the site allows 35 downloads in 30 seconds and 100 in 6 hours per address; bookfetch keeps a small log (`$XDG_STATE_HOME/bookfetch/`, usually `~/.local/state/bookfetch/`) and stays under both. Members of the Patrons Circle can set their account email (see below), which lifts that limit. Formats: `epub` (default), `advanced` (EPUB 3 for newer readers), `kepub` (Kobo), `azw3` (Kindle). Ids look like `standardebooks:jane-austen/emma` or `standardebooks:jane-austen/emma:azw3`.

**Open Library / Internet Archive** (openlibrary.org, archive.org). Search uses Open Library and asks only for books with a public (free) ebook, then checks the matches against the Internet Archive and drops audio recordings and lending-only scans. A download picks a public EPUB, PDF or plain-text file from the archive.org item and checks its MD5. Books that are only available for borrowing, and files protected with DRM, are never downloaded. These are usually scans of old printed books; the PDF is the faithful scan, the EPUB and text are machine-read (OCR) and can contain errors. bookfetch waits about 1 second between requests to each service. Ids are archive.org identifiers, for example `openlibrary:IDENTIFIER` or `openlibrary:IDENTIFIER:pdf`.

**arXiv** (arxiv.org). Search uses the official arXiv API; you can also search for an id or paste an arXiv link. arXiv asks for no more than one request every 3 seconds, so bookfetch waits that long between requests, including between a search and a download in the same run. If arXiv answers with HTTP 403, your address is blocked; bookfetch stops instead of retrying. Downloads are PDFs. Ids are arXiv ids with or without a version, including old-style ones: `arxiv:1706.03762`, `arxiv:hep-th/9901001v3`.

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
bookfetch download gutenberg:1342 --out ~/Books
bookfetch download standardebooks:jane-austen/pride-and-prejudice --out ~/Books
bookfetch search "attention is all you need" --source arxiv --limit 3
bookfetch search "frankenstein" --source standardebooks,gutenberg
bookfetch download arxiv:1706.03762 --out ~/Papers
bookfetch quota
bookfetch accounts
```

`search` asks every available source at the same time (or only the ones given with `--source`, e.g. `--source gutenberg,standardebooks`) and prints each result's title, author, publisher, a summary line (language, format, size, year) and an id such as `annas:MD5`, `zlib:ID/HASH` or `gutenberg:1342`. Pass that id to `download` exactly as printed. `--limit` applies per source and defaults to 5 when searching several sources and 10 for a single one. If one source fails, its error goes to standard error and the other results are still shown. `--json` prints all results in one list with the same fields for every source. For the open sources the year is the one the source reports: Gutenberg gives none, Standard Ebooks gives the year of its edition, and Open Library gives the year of the scanned printing when known.

`download` saves the file and prints its path. For Anna's Archive and Z-Library it also prints how many downloads that account has left today; every download there counts against your daily allowance, even if the transfer fails afterwards, so pick the file before you download it. With the open sources, a file arrives under a temporary name and is only renamed once it is complete, and bookfetch refuses to save a web page in place of a book.

`quota` shows recent download usage for every configured account, one line each, labelled like `annas/main`. For the open sources it shows their limits instead. An account that fails shows its error on its own line and the others are still listed.

`accounts` (or `config`) lists the accounts for each source, where each one comes from, and the domains in use. The open sources show a built-in account called `anonymous`. It never prints keys, passwords or the Standard Ebooks email.

Exit codes: 0 when at least one source worked, 1 when every source that was tried failed, 2 when no source was available.

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
| `STANDARDEBOOKS_EMAIL` | Optional Standard Ebooks Patrons Circle email. Only needed to lift the anonymous download limit. |
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

# Optional: Standard Ebooks Patrons Circle members. Login is the email with an empty password.
[standardebooks.accounts.me]
email = "me@example.com"

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

Project Gutenberg, Open Library and arXiv have no accounts in bookfetch. Standard Ebooks uses its configured Patrons Circle accounts first and then the built-in `anonymous` account. The Patrons Circle email works like a password for that site, so keep the config file private.

## Use only official domains

The open sources use fixed official addresses (gutenberg.org, standardebooks.org, openlibrary.org, archive.org, arxiv.org). Anna's Archive and Z-Library have many fake copies, and some exist to collect logins and keys. The built-in domains are the official ones. If they stop working, check the current list on Wikipedia ([Anna's Archive](https://en.wikipedia.org/wiki/Anna%27s_Archive), [Z-Library](https://en.wikipedia.org/wiki/Z-Library)) before setting `ANNAS_DOMAINS` or `ZLIB_DOMAINS`, and never enter your key or password on a site that is not listed there.

## Copyright

Copyright law differs from country to country. Books on Project Gutenberg and Standard Ebooks are public domain in the United States, but not necessarily everywhere. You are responsible for making sure that what you download is legal where you live.

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
