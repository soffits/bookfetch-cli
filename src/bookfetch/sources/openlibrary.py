"""Open Library search with files from the Internet Archive (archive.org).

Only public items are offered: search asks Open Library for works with a
public ebook, then one archive.org query drops audio items (LibriVox) and
access-restricted (lending-only) items. A download reads the item's file list
and picks a public, unencrypted EPUB, PDF or text file; lending copies and DRM
files (LCP, ACS) are never fetched. Requests are spaced about 1 second apart.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from ..common import BookfetchError, result
from ..net import Http, HttpError, Rule
from .base import OpenSource, no_account

NAME = "openlibrary"
ANONYMOUS = True
DEFAULT_DOMAINS = ["openlibrary.org", "archive.org"]
DOMAINS_ENV = ""
ACCOUNT_FIELDS: tuple[str, ...] = ()
SECRET_FIELDS: tuple[str, ...] = ()
NOT_CONFIGURED = "works without an account"
env_account = no_account

OL = "https://openlibrary.org"
IA = "https://archive.org"
INTERVAL = 1.0
# Asking for editions.* drops the availability field, so only work-level fields are used.
FIELDS = "key,title,author_name,first_publish_year,language,ia,ebook_access,availability"
IA_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{1,99}"
ID_RE = re.compile(rf"({IA_ID})(?::(epub|pdf|txt))?")
# --ext -> archive.org file formats, best first.
FORMATS = {
    "epub": ("EPUB",),
    "pdf": ("Text PDF", "Additional Text PDF", "Image Container PDF", "PDF"),
    "txt": ("DjVuTXT",),
}
EXTENSIONS = {"EPUB": "epub", "DjVuTXT": "txt"}
DRM_NAME_RE = re.compile(r"(_lcp\.epub|\.lcpdf|_encrypted\.pdf|\.acsm)$", re.I)
MAGIC = {"epub": b"PK", "pdf": b"%PDF"}
# ISO 639-1 -> the MARC codes Open Library uses.
LANGUAGES = {
    "en": "eng", "fr": "fre", "de": "ger", "es": "spa", "it": "ita", "pt": "por", "ru": "rus", "zh": "chi", "ja": "jpn",
    "nl": "dut", "la": "lat", "pl": "pol", "sv": "swe", "fi": "fin", "da": "dan", "no": "nor", "el": "gre", "ko": "kor",
    "ar": "ara", "he": "heb", "hi": "hin", "cs": "cze", "hu": "hun", "tr": "tur", "uk": "ukr",
}  # fmt: skip

RULES = [
    Rule(r"openlibrary\.org", r"/search\.json"),
    Rule(r"archive\.org", r"/advancedsearch\.php"),
    Rule(r"archive\.org", rf"/metadata/{IA_ID}"),
    Rule(r"archive\.org", rf"/download/{IA_ID}/[^?#]+"),
    # /download/ redirects to the data server that holds the item.
    Rule(r"[a-z0-9-]+(\.[a-z0-9-]+)?\.archive\.org", rf"/(\d+/)?items/{IA_ID}/[^?#]+"),
]


class OpenLibraryError(BookfetchError):
    """An Open Library or Internet Archive error."""


def host_group(host: str) -> str:
    return "openlibrary.org" if host == "openlibrary.org" else "archive.org"


def parse_id(value: str) -> tuple[str, str | None]:
    """'IDENTIFIER' or 'IDENTIFIER:pdf' -> (identifier, ext or None)."""
    m = ID_RE.fullmatch(value.strip())
    if not m:
        raise OpenLibraryError(f"not an archive.org identifier: {value!r}")
    return m.group(1), m.group(2)


def ext_key(ext: str | None) -> str | None:
    if not ext:
        return None
    key = ext.lower().lstrip(".")
    return key if key in FORMATS else ""


def search_url(query: str, lang: str | None, limit: int) -> str:
    q = query.strip()
    marc = LANGUAGES.get((lang or "").lower()[:2], (lang or "").lower()) if lang else ""
    if marc:
        q += f" language:{marc}"
    params = {"q": q, "ebook_access": "public", "has_fulltext": "true", "fields": FIELDS, "limit": str(min(max(limit, 1) * 2, 40))}
    return f"{OL}/search.json?" + urllib.parse.urlencode(params)


def candidates(doc: dict) -> list[str]:
    """archive.org identifiers worth checking for one work, best first (a few at most).

    The work's ``availability`` names its best open scan. The work's ``ia`` list
    mixes public and lending-only scans and audio items, so those are only
    candidates; the archive.org check in ``public_text_items`` sorts them out.
    """
    found: list[str] = []
    avail = doc.get("availability") or {}
    if avail.get("status") == "open" and not avail.get("is_restricted") and avail.get("identifier"):
        found.append(avail["identifier"])
    found.extend((doc.get("ia") or [])[:3])
    return [i for i in dict.fromkeys(found) if re.fullmatch(IA_ID, i)][:3]


def parse_search(data: bytes) -> list[dict]:
    """Works with candidate identifiers, before the archive.org check."""
    try:
        docs = json.loads(data).get("docs") or []
    except (ValueError, AttributeError):
        raise OpenLibraryError("Open Library search returned something that isn't JSON") from None
    works = []
    for doc in docs:
        if doc.get("ebook_access") != "public":
            continue
        ids = candidates(doc)
        if ids:
            works.append({"doc": doc, "ids": ids})
    return works


def items_url(ids: list[str]) -> str:
    params = [("q", "identifier:(" + " OR ".join(ids) + ")")]
    params += [
        ("fl[]", f) for f in ("identifier", "mediatype", "format", "access-restricted-item", "title", "date", "language", "publisher")
    ]
    params += [("rows", str(len(ids))), ("output", "json")]
    return f"{IA}/advancedsearch.php?" + urllib.parse.urlencode(params)


def public_text_items(data: bytes) -> dict[str, dict]:
    """identifier -> item for items that are books (not audio) and not access-restricted."""
    try:
        docs = json.loads(data)["response"]["docs"]
    except (ValueError, KeyError, TypeError):
        raise OpenLibraryError("archive.org search returned something unexpected") from None
    return {
        d["identifier"]: d
        for d in docs
        if d.get("mediatype") == "texts" and str(d.get("access-restricted-item", "")).lower() != "true" and d.get("identifier")
    }


def item_ext(formats: list[str], wanted: str | None) -> str:
    for key in [wanted] if wanted else ["epub", "pdf", "txt"]:
        if any(f in formats for f in FORMATS[key]):
            return key
    return ""


def first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def to_rows(works: list[dict], items: dict[str, dict], ext: str | None, limit: int) -> list[dict]:
    rows = []
    for work in works:
        for ident in work["ids"]:
            item = items.get(ident)
            if not item:
                continue
            formats = item.get("format") or []
            formats = [formats] if isinstance(formats, str) else formats
            found = item_ext(formats, ext)
            if not found:
                continue
            doc = work["doc"]
            year = re.search(r"\d{4}", first(item.get("date")))
            rows.append(
                result(
                    NAME,
                    ident if not ext else f"{ident}:{found}",
                    title=doc.get("title") or first(item.get("title")),
                    author=", ".join(doc.get("author_name") or []),
                    publisher=first(item.get("publisher")),
                    year=year.group(0) if year else doc.get("first_publish_year", ""),
                    language=first(item.get("language")),
                    extension=found,
                    extra={
                        "work": doc.get("key", ""),
                        "first_publish_year": doc.get("first_publish_year", ""),
                        "url": f"{IA}/details/{ident}",
                    },
                )
            )
            break
        if len(rows) >= limit:
            break
    return rows


def choose_file(meta: dict, wanted: str | None) -> tuple[dict, str]:
    """The public, unencrypted file to download and its extension; raises if there is none."""
    m = meta.get("metadata") or {}
    ident = m.get("identifier", "this item")
    if not meta.get("files"):
        raise OpenLibraryError(f"no archive.org item {ident}")
    if meta.get("is_dark") or str(m.get("access-restricted-item", "")).lower() == "true":
        raise OpenLibraryError(f"{ident} is lending-only (access-restricted); bookfetch only downloads public files")
    if m.get("mediatype") != "texts":
        raise OpenLibraryError(f"{ident} is not a book (mediatype {m.get('mediatype') or 'unknown'}); pick another result")
    files = [
        f
        for f in meta["files"]
        if not f.get("private") and not DRM_NAME_RE.search(f.get("name", "")) and "encrypted" not in f.get("format", "").lower()
    ]
    for key in [wanted] if wanted else ["epub", "pdf", "txt"]:
        for fmt in FORMATS[key]:
            for f in files:
                if f.get("format") == fmt and f.get("name"):
                    return f, key
    what = wanted.upper() if wanted else "EPUB, PDF or text"
    raise OpenLibraryError(f"{ident} has no public {what} file")


class Source(OpenSource):
    name = NAME
    id_help = "IDENTIFIER[:epub|pdf|txt]"
    limits = "no account quota; bookfetch waits about 1 s between requests (Open Library's guidance)"

    def make_http(self) -> Http:
        return Http(NAME, RULES, intervals={"openlibrary.org": INTERVAL, "archive.org": INTERVAL}, groups=host_group)

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict]:
        key = ext_key(ext)
        if key == "" or limit <= 0:
            return []
        works = parse_search(self.http.get(search_url(query, lang, limit)))
        ids = [i for w in works for i in w["ids"]][:100]
        if not ids:
            return []
        items = public_text_items(self.http.get(items_url(ids)))
        return to_rows(works, items, key, limit)

    def download(self, id: str, out: str | Path = ".", name: str | None = None) -> Path:
        ident, wanted = parse_id(id)
        try:
            meta = json.loads(self.http.get(f"{IA}/metadata/{ident}"))
        except ValueError:
            raise OpenLibraryError("archive.org returned something that isn't JSON") from None
        meta.setdefault("metadata", {}).setdefault("identifier", ident)
        f, ext = choose_file(meta, wanted)
        url = f"{IA}/download/{ident}/" + urllib.parse.quote(f["name"])
        size = str(f.get("size") or "")
        try:
            return self.http.download(
                url,
                out,
                name=name or f["name"],
                fallback=f"{ident}.{ext}",
                md5=f.get("md5"),
                size=int(size) if size.isdigit() else None,
                magic=MAGIC.get(ext),
            )
        except HttpError as e:
            if e.status in (401, 403):
                raise OpenLibraryError(f"archive.org refused {f['name']} (HTTP {e.status}); it is probably lending-only") from None
            raise
