---
name: bookfetch
description: "Use when finding or downloading a book or paper from Anna's Archive or Z-Library with the bookfetch CLI."
---

# Books via the `bookfetch` CLI

`bookfetch` searches Anna's Archive and Z-Library and downloads files with the user's own accounts. Accounts come from environment variables (`ANNAS_KEY_FILE`, `ZLIB_EMAIL`, `ZLIB_PASSWORD_FILE`, `BOOKFETCH_CDP_URL`, ...), which define an account named `env`, and from an optional TOML config file (`--config`, `BOOKFETCH_CONFIG`, or `~/.config/bookfetch/config.toml`) that can hold several named accounts per source; see the README. Never print, echo, or paste a key, password, or cookie; point the tool at files instead.

```bash
bookfetch search "title author" [--source annas|zlib|all] [--ext epub|pdf] [--lang en] [--limit 5] [--json]
bookfetch download SOURCE:ID --out DIR [--name FILE]
bookfetch quota [--source annas|zlib|all]
bookfetch accounts            # configured accounts and where they come from; no secrets
# any command: --account NAME (use only that account), --config PATH
```

## Workflow

1. Search all sources with the title and author. Add `--ext` and `--lang` when the user named a format or language. Use `--json` when you need to process results. A note on stderr like `zlib: skipped, ...` means that source isn't configured; an error line for one source doesn't invalidate the others' results.
2. Show the user the candidates: title, author, publisher, summary (language, format, size, year), and the id. If several are plausible, let the user pick. Otherwise prefer the requested language and format, a real publisher edition over a scan, and EPUB for reading.
3. Download only the chosen id, passing it exactly as printed (`annas:MD5` or `zlib:ID/HASH`), into a directory the user expects.
4. Check the file: an EPUB is a zip whose `mimetype` entry is `application/epub+zip`; a PDF starts with `%PDF`. If it's HTML or tiny, the download failed; say so.

## Accounts

Run `bookfetch accounts` to see which accounts exist. Without `--account`, search uses each source's first account and moves to the next only if login fails; download moves to the next account only if the first has no downloads left today or can't log in, and prints which account it used (`annas/spare downloads left today: ...`). Use `--account NAME` when the user wants a specific account; then there is no fallback. Only use accounts the user owns or may use.

## Quota

Every `download` spends one download from that source's daily allowance, even if the transfer fails afterwards. Never call `download` to test or probe. Run `bookfetch quota` before a batch; it prints one line per account (`source/account: ...`), and a broken account shows `error: ...` without hiding the others.

- Anna's Archive: fast downloads per day depend on the membership tier.
- Z-Library: free accounts get 10 downloads per day.

When the same book is on both, pick the source with more allowance left, or the better file. If quota is low or a membership has lapsed, tell the user before downloading.

## Errors

- Exit 2: no source configured (or the chosen one isn't).
- Exit 1: every configured source failed; the reasons are on stderr.
- Anna's `login failed`: key rejected or no domain reachable. HTTP 403 on search usually means the membership isn't active.
- Z-Library `browser check blocked the request`: search and file lookups need `BOOKFETCH_CDP_URL` pointing at a Chromium started with `--remote-debugging-port` that has opened Z-Library once. Don't try to work around the check another way.
- `no download link`: the Z-Library daily limit is used up or the file is unavailable.

## Official domains only

Fake mirrors of both sites are common, and some collect credentials. Only use domains listed on Wikipedia's "Anna's Archive" and "Z-Library" articles. If the built-in domains stop working, check Wikipedia and set `ANNAS_DOMAINS` or `ZLIB_DOMAINS`. Never send credentials anywhere else.

## Copyright

The user is responsible for following the copyright law where they live. Help them find what they ask for; mention it if a request looks clearly unlawful where they are.
