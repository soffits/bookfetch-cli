---
name: bookfetch
description: "Use when finding or downloading a book or paper with the bookfetch CLI (Anna's Archive, Z-Library, Project Gutenberg, Standard Ebooks, Open Library, arXiv)."
---

# Books and papers via the `bookfetch` CLI

`bookfetch` searches six sources and downloads files:

- **Open sources, no account needed**: Project Gutenberg (`gutenberg`), Standard Ebooks (`standardebooks`), Open Library / Internet Archive (`openlibrary`), arXiv (`arxiv`). They use a built-in account called `anonymous`.
- **Account sources**: Anna's Archive (`annas`, membership key) and Z-Library (`zlib`, email and password, plus a Chromium with remote debugging for search). Accounts come from environment variables (`ANNAS_KEY_FILE`, `ZLIB_EMAIL`, `ZLIB_PASSWORD_FILE`, `BOOKFETCH_CDP_URL`, ...), which define an account named `env`, and from an optional TOML config file (`--config`, `BOOKFETCH_CONFIG`, or `~/.config/bookfetch/config.toml`) that can hold several named accounts per source; see the README.

Never print, echo, or paste a key, password, cookie, or Standard Ebooks patron email; point the tool at files instead.

```bash
bookfetch search "title author" [--source NAME[,NAME...]|all] [--ext epub|pdf|txt|azw3] [--lang en] [--limit 5] [--json]
bookfetch download SOURCE:ID --out DIR [--name FILE]
bookfetch quota [--source NAME[,NAME...]|all]
bookfetch accounts            # accounts and where they come from; no secrets
# any command: --account NAME (use only that account), --config PATH
```

## Which source

- Public-domain books: try `standardebooks` first (best editions, EPUB), then `gutenberg` (largest public-domain catalog). Both are free and have no quota.
- Older books that only exist as scans: `openlibrary` (public scans from the Internet Archive; the PDF is the faithful scan, EPUB and text are OCR).
- Research papers on arXiv: `arxiv` (search by title words, by id such as `1706.03762` or `hep-th/9901001`, or paste an arXiv link).
- Everything else (in-copyright books, papers not on arXiv): `annas` or `zlib`, which spend a daily quota.

## Workflow

1. Search with the title and author. Without `--source` all sources are searched in parallel, 5 results each (a comma list like `--source standardebooks,gutenberg` searches just those); add `--ext` and `--lang` when the user named a format or language. Use `--json` when you need to process results. A note on stderr like `zlib: skipped, ...` means that source isn't configured; an error line for one source doesn't invalidate the others' results.
2. Show the user the candidates: title, author, publisher, summary (language, format, size, year), and the id. If several are plausible, let the user pick. Otherwise prefer the requested language and format, a proper edition (Standard Ebooks, a publisher edition) over a scan, and EPUB for reading.
3. Download only the chosen id, passing it exactly as printed (`annas:MD5`, `zlib:ID/HASH`, `gutenberg:1342`, `standardebooks:jane-austen/emma`, `openlibrary:IDENTIFIER`, `arxiv:hep-th/9901001v3`), into a directory the user expects. Open-source ids can carry a format: `gutenberg:1342:txt`, `standardebooks:jane-austen/emma:azw3`, `openlibrary:IDENTIFIER:pdf`.
4. Check the file: an EPUB is a zip whose `mimetype` entry is `application/epub+zip`; a PDF starts with `%PDF`. If it's HTML or tiny, the download failed; say so.

## Be polite to the open sources

They are free public services. bookfetch already waits between requests (arXiv 3 s, Gutenberg and Standard Ebooks 2 s, Open Library 1 s) and only requests fixed addresses. Don't loop over many searches or downloads to collect a catalog, and don't fetch their web pages another way. If arXiv returns 403, the address is blocked: stop. Standard Ebooks allows 35 anonymous downloads per 30 s and 100 per 6 h per address; bookfetch stays under that unless a Patrons Circle email is configured (`STANDARDEBOOKS_EMAIL` or `[standardebooks.accounts.NAME] email = "..."`).

Open Library only offers public files. Lending-only books and DRM files are refused on purpose; don't try to get them another way.

## Accounts and quota (Anna's Archive, Z-Library)

Run `bookfetch accounts` to see which accounts exist. Without `--account`, search uses each source's first account and moves to the next only if login fails; download moves to the next account only if the first has no downloads left today or can't log in, and prints which account it used (`annas/spare downloads left today: ...`). Use `--account NAME` when the user wants a specific account; then there is no fallback. Only use accounts the user owns or may use.

Every `annas` or `zlib` download spends one download from that account's daily allowance, even if the transfer fails afterwards. Never call `download` to test or probe. Run `bookfetch quota` before a batch; it prints one line per account (`source/account: ...`), and a broken account shows `error: ...` without hiding the others. For the open sources it prints their limits.

- Anna's Archive: fast downloads per day depend on the membership tier.
- Z-Library: free accounts get 10 downloads per day.

When the same book is on an open source, prefer that and keep the quota. Between Anna's and Z-Library, pick the source with more allowance left, or the better file. If quota is low or a membership has lapsed, tell the user before downloading.

## Errors

- Exit 2: no source available (or the chosen one isn't configured).
- Exit 1: every source that was tried failed; the reasons are on stderr.
- Anna's `login failed`: key rejected or no domain reachable. HTTP 403 on search usually means the membership isn't active.
- Z-Library `browser check blocked the request`: search and file lookups need `BOOKFETCH_CDP_URL` pointing at a Chromium started with `--remote-debugging-port` that has opened Z-Library once. Don't try to work around the check another way.
- `no download link`: the Z-Library daily limit is used up or the file is unavailable.
- `got a web page instead of the file`: nothing was saved; the site served a page instead. Report it rather than retrying in a loop.
- `rate limited`: wait the time given, then try once more.

## Official domains only

Fake mirrors of Anna's Archive and Z-Library are common, and some collect credentials. Only use domains listed on Wikipedia's "Anna's Archive" and "Z-Library" articles. If the built-in domains stop working, check Wikipedia and set `ANNAS_DOMAINS` or `ZLIB_DOMAINS`. Never send credentials anywhere else. The open sources use fixed official addresses.

## Copyright

The user is responsible for following the copyright law where they live. Public-domain status on Gutenberg and Standard Ebooks is for the United States. Help them find what they ask for; mention it if a request looks clearly unlawful where they are.
