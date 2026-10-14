"""arXiv: search through the official API (export.arxiv.org), PDFs from arxiv.org.

arXiv's API terms ask for no more than one request every three seconds on a
single connection, so every request to either host waits for that gap. A 403
means arXiv has blocked the address; bookfetch stops and says so.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from .. import feeds
from ..common import BookfetchError, result
from ..net import Http, HttpError, Rule
from .base import OpenSource, no_account

NAME = "arxiv"
ANONYMOUS = True
DEFAULT_DOMAINS = ["arxiv.org", "export.arxiv.org"]
DOMAINS_ENV = ""
ACCOUNT_FIELDS: tuple[str, ...] = ()
SECRET_FIELDS: tuple[str, ...] = ()
NOT_CONFIGURED = "works without an account"
env_account = no_account

API = "https://export.arxiv.org/api/query"
PDF = "https://arxiv.org/pdf/"
INTERVAL = 3.0
MAX_RESULTS = 50
NEW_ID = r"\d{4}\.\d{4,5}(?:v\d+)?"
OLD_ID = r"[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
ID_RE = re.compile(rf"(?:[Aa][Rr][Xx][Ii][Vv]:)?({NEW_ID}|{OLD_ID})")
URL_RE = re.compile(rf"https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/({NEW_ID}|{OLD_ID})(?:\.pdf)?/?")
FIELD_RE = re.compile(r"\b(ti|au|abs|co|jr|cat|rn|id|all):", re.I)
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "you",
    "all",
}

RULES = [
    Rule(r"export\.arxiv\.org", r"/api/query"),
    Rule(r"arxiv\.org", rf"/pdf/(?:{NEW_ID}|{OLD_ID})"),
]


class ArxivError(BookfetchError):
    """An arXiv error."""


def parse_id(value: str) -> str:
    """An arXiv id from an id, 'arxiv:ID', or an abs/pdf URL; keeps the version and old-style ids like hep-th/9901001."""
    value = value.strip()
    m = URL_RE.fullmatch(value) or ID_RE.fullmatch(value)
    if not m:
        raise ArxivError(f"not an arXiv id: {value!r} (expected e.g. 1706.03762, 1706.03762v7 or hep-th/9901001)")
    return m.group(1)


def build_queries(query: str) -> list[dict[str, str]]:
    """API parameter sets to try in order.

    An id or abs/pdf URL becomes an id lookup and raw field syntax (ti:, au:,
    cat:, ...) is passed through. Free text first tries the words as a title
    phrase, which ranks well-known papers first; if that finds nothing, all the
    significant words must appear somewhere.
    """
    q = query.strip()
    try:
        return [{"id_list": parse_id(q)}]
    except ArxivError:
        pass
    if FIELD_RE.search(q):
        return [{"search_query": q}]
    words = re.findall(r"[\w'-]+", q.lower())
    terms = [w for w in words if w not in STOPWORDS] or words
    every = {"search_query": " AND ".join(f"all:{w}" for w in terms)}
    if len(words) > 1:
        return [{"search_query": 'ti:"' + " ".join(words) + '"'}, every]
    return [every]


def search_url(params: dict[str, str], limit: int) -> str:
    params = {**params, "start": "0", "max_results": str(min(max(limit, 1), MAX_RESULTS))}
    if "search_query" in params:
        params.update(sortBy="relevance", sortOrder="descending")
    return API + "?" + urllib.parse.urlencode(params)


def short_id(entry_id: str) -> str:
    return entry_id.split("/abs/", 1)[-1].strip()


def byline(authors: list[str]) -> str:
    return ", ".join(authors) if len(authors) <= 3 else ", ".join(authors[:3]) + " et al."


def parse_search(data: bytes) -> list[dict]:
    rows = []
    for entry in feeds.parse(data).entries:
        if "/abs/" not in entry.id:
            continue  # the API reports query errors as an entry without an abs link
        ident = short_id(entry.id)
        x = entry.extra
        rows.append(
            result(
                NAME,
                ident,
                title=entry.title,
                author=byline(entry.authors),
                publisher=x.get("journal_ref") or "arXiv",
                year=entry.published[:4],
                extension="pdf",
                extra={
                    "authors": entry.authors,
                    "primary_category": x.get("primary_category", ""),
                    "doi": x.get("doi", ""),
                    "abs_url": f"https://arxiv.org/abs/{ident}",
                    "published": entry.published,
                    "updated": entry.updated,
                },
            )
        )
    return rows


class Source(OpenSource):
    name = NAME
    id_help = "ARXIV_ID"
    limits = "no account quota; arXiv allows 1 API request every 3 s, bookfetch keeps that gap"

    def make_http(self) -> Http:
        return Http(NAME, RULES, intervals={"arxiv.org": INTERVAL}, groups={"export.arxiv.org": "arxiv.org"})

    def _get(self, url: str) -> bytes:
        try:
            return self.http.get(url, accept="application/atom+xml")
        except HttpError as e:
            raise blocked(e) from None

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict]:
        if ext and ext.lower().lstrip(".") != "pdf":
            return []
        if lang and not lang.lower().startswith("en"):
            return []
        rows: list[dict] = []
        for params in build_queries(query):
            rows = parse_search(self._get(search_url(params, limit)))
            if rows:
                break
        return rows[: max(limit, 0)]

    def download(self, id: str, out: str | Path = ".", name: str | None = None) -> Path:
        ident = parse_id(id)
        fallback = ident.replace("/", "_") + ".pdf"
        try:
            return self.http.download(PDF + ident, out, name=name or fallback, fallback=fallback, magic=b"%PDF")
        except HttpError as e:
            if e.status == 404:
                raise ArxivError(f"no arXiv paper {ident}") from None
            raise blocked(e) from None


def blocked(e: HttpError) -> BookfetchError:
    if e.status == 403:
        return ArxivError("arXiv refused the request (HTTP 403), which usually means this address is blocked; stop and try much later")
    return e
