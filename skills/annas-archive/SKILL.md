---
name: annas-archive
description: "Use when finding or downloading a book or paper from Anna's Archive with the annas CLI."
---

# Anna's Archive via the `annas` CLI

`annas` searches Anna's Archive and downloads files through the member fast-download API. It needs an active membership key, provided through `ANNAS_KEY`, `ANNAS_KEY_FILE`, or `--key-file`. Never print, echo, or paste the key; point the tool at the file instead.

```bash
annas search "title author" [--ext epub|pdf] [--lang en] [--limit 10] [--json]
annas download MD5 --out DIR [--name FILE]
annas quota
```

## Workflow

1. Search with the title and author. Add `--ext` and `--lang` when the user named a format or language. Use `--json` when you need to process results.
2. Show the user the candidates: title, author, publisher, the summary line (language, format, size, year), and MD5. If several are plausible, let the user pick. Otherwise prefer the requested language and format, a real publisher edition over a scan, and EPUB for reading.
3. Download only the chosen MD5, into a working directory the user expects.
4. Check the file: an EPUB is a zip whose `mimetype` entry is `application/epub+zip`; a PDF starts with `%PDF`.

## Quota

Each `download` call spends one fast download from the member's daily allowance, even if the transfer fails afterwards. Do not call `download` to test or probe; pick the file first. Run `annas quota` to check the membership tier and recent usage. If the quota is low or the membership has lapsed, tell the user before downloading.

## Errors

- Exit 2: no key configured.
- Exit 1 with `login failed`: the key was rejected or no domain was reachable.
- An HTTP 403 on search usually means the membership is not active; search from a script only works for members.
- `API error: ...` comes from the download API; report the message to the user.

## Official domains only

Fake Anna's Archive mirrors are common, and some collect keys. Only use the domains listed on Wikipedia's "Anna's Archive" article. If the built-in list stops working, check Wikipedia and set `ANNAS_DOMAINS=a,b,c`. Never send the key to any other domain.

## Copyright

The user is responsible for following the copyright law where they live. Help them find what they ask for; mention it if a request looks clearly unlawful where they are.
