"""Project Gutenberg through its official OPDS catalog feed.

Gutenberg asks programs to use its catalog feeds rather than the web pages, so
search uses the OPDS search feed and nothing else. A download makes one request
for the book's own OPDS entry (title, author, sizes) and then fetches the file.
Requests to gutenberg.org are spaced at least 2 seconds apart.

Gutenberg plans to retire this XML feed in 2027.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from .. import feeds
from ..common import BookfetchError, result
from ..net import Http, Rule
from .base import OpenSource, no_account

NAME = "gutenberg"
ANONYMOUS = True
DEFAULT_DOMAINS = ["www.gutenberg.org"]
DOMAINS_ENV = ""
ACCOUNT_FIELDS: tuple[str, ...] = ()
SECRET_FIELDS: tuple[str, ...] = ()
NOT_CONFIGURED = "works without an account"
env_account = no_account

BASE = "https://www.gutenberg.org"
INTERVAL = 2.0
# --ext value -> (Gutenberg format suffix, file extension). The first is the default.
FORMATS = {
    "epub": ("epub3.images", "epub"),
    "epub-noimages": ("epub.noimages", "epub"),
    "azw3": ("kf8.images", "azw3"),
    "mobi": ("kindle.images", "mobi"),
    "txt": ("txt.utf-8", "txt"),
    "html": ("html.images", "zip"),
}
ALIASES = {"epub3": "epub", "kindle": "azw3", "kf8": "azw3", "text": "txt", "htm": "html"}
MAGIC = {"epub": b"PK", "azw3": None, "mobi": None, "txt": None, "zip": b"PK"}
BOOK_RE = re.compile(r"/ebooks/(\d+)\.opds$")
ID_RE = re.compile(r"(\d+)(?::([a-z0-9-]+))?")
DOWNLOADS_RE = re.compile(r"\d+ downloads?")
LANG_SUFFIX_RE = re.compile(r"\s+\(([A-Z][a-z]+(?: [A-Z][a-z]+)?)\)$")

RULES = [
    Rule(r"(www\.)?gutenberg\.org", r"/ebooks/search\.opds/"),
    Rule(r"(www\.)?gutenberg\.org", r"/ebooks/\d+\.opds"),
    Rule(r"(www\.)?gutenberg\.org", r"/ebooks/\d+\.(" + "|".join(re.escape(f) for f, _ in FORMATS.values()) + r")"),
    Rule(r"(www\.)?gutenberg\.org", r"/cache/epub/\d+/[A-Za-z0-9._-]+"),
]


class GutenbergError(BookfetchError):
    """A Project Gutenberg error."""


def format_key(ext: str | None) -> str | None:
    """The FORMATS key for an --ext value; None if Gutenberg doesn't offer it."""
    key = (ext or "epub").lower().lstrip(".")
    key = ALIASES.get(key, key)
    return key if key in FORMATS else None


def parse_id(value: str) -> tuple[str, str]:
    """'1342' or '1342:txt' -> ('1342', 'txt')."""
    m = ID_RE.fullmatch(value.strip().lower())
    key = format_key(m.group(2)) if m else None
    if not m or not key:
        raise GutenbergError(f"not a Gutenberg id: {value!r} (expected a number, optionally with :FORMAT, e.g. 1342 or 1342:txt)")
    return m.group(1), key


def search_url(query: str, lang: str | None) -> str:
    q = query.strip()
    if lang:
        q += f" l.{lang.strip().lower()}"
    return f"{BASE}/ebooks/search.opds/?" + urllib.parse.urlencode({"query": q})


def parse_search(data: bytes, ext: str | None = None) -> list[dict]:
    """Book entries from a search feed (author, subject and other navigation entries are skipped)."""
    key = format_key(ext)
    if not key:
        return []
    rows = []
    for entry in feeds.parse(data).entries:
        m = BOOK_RE.search(entry.id)
        if not m:
            continue
        title, language = entry.title, ""
        lm = LANG_SUFFIX_RE.search(title)
        if lm:
            title, language = title[: lm.start()], lm.group(1)
        author = "" if DOWNLOADS_RE.fullmatch(entry.content) else entry.content
        ident = m.group(1) if key == "epub" else f"{m.group(1)}:{key}"
        rows.append(
            result(
                NAME,
                ident,
                title=title,
                author=author,
                publisher="Project Gutenberg",
                language=language,
                extension=FORMATS[key][1],
                extra={"url": f"{BASE}/ebooks/{m.group(1)}"},
            )
        )
    return rows


def parse_book(data: bytes, number: str) -> dict:
    """Title, author, language and {format suffix: size} from a book's own OPDS feed."""
    info = {"title": "", "author": "", "language": "", "sizes": {}}
    for entry in feeds.parse(data).entries:
        info["title"] = info["title"] or entry.title
        info["author"] = info["author"] or "; ".join(entry.authors)
        info["language"] = info["language"] or entry.language
        for link in entry.acquisitions():
            m = re.search(rf"/ebooks/{number}\.([a-z0-9.-]+)$", link.href)
            if m:
                info["sizes"][m.group(1)] = link.length
    return info


def file_name(info: dict, number: str, extension: str) -> str:
    author = info["author"].split(";")[0].strip()
    stem = " - ".join(x for x in (info["title"], author) if x) or f"pg{number}"
    return f"{stem}.{extension}"


class Source(OpenSource):
    name = NAME
    id_help = "NUMBER[:FORMAT]"
    limits = "no account quota; bookfetch waits 2 s between requests (Gutenberg allows catalog feeds, not crawling)"

    def make_http(self) -> Http:
        return Http(NAME, RULES, intervals={"www.gutenberg.org": INTERVAL}, groups={"gutenberg.org": "www.gutenberg.org"})

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict]:
        if not format_key(ext):
            return []
        data = self.http.get(search_url(query, lang), accept="application/atom+xml")
        return parse_search(data, ext)[: max(limit, 0)]

    def download(self, id: str, out: str | Path = ".", name: str | None = None) -> Path:
        number, key = parse_id(id)
        suffix, extension = FORMATS[key]
        info = parse_book(self.http.get(f"{BASE}/ebooks/{number}.opds", accept="application/atom+xml"), number)
        if not info["title"]:
            raise GutenbergError(f"no book {number} on Project Gutenberg")
        listed = info["sizes"]
        if suffix.startswith(("epub", "kf8", "kindle")) and listed and suffix not in listed:
            offered = ", ".join(k for k, (s, _) in FORMATS.items() if s in listed)
            raise GutenbergError(f"book {number} has no {key} file (offered: {offered or 'none'})")
        return self.http.download(
            f"{BASE}/ebooks/{number}.{suffix}",
            out,
            name=name or file_name(info, number, extension),
            fallback=f"pg{number}.{extension}",
            magic=MAGIC.get(extension),
        )
