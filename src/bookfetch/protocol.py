"""``bookfetch source``: bookfetch as a source for media hubs.

Speaks the JSON source protocol of dsh-media-hub (docs/source-protocol.md
there): ``search``, ``info``, ``download`` and ``cover``, each with
``--json``. A work's id is ``SOURCE:ID`` without a format (``gutenberg:1342``);
``download --format`` picks the file. Kinds: ``ebook`` (every book source)
and ``paper`` (arXiv). Errors go to stderr, the last line saying what failed;
a search where only some sources fail still answers with the others.

Network rules stay the ones of each source: the proxy variables
(``ANNAS_PROXY``, ``ZLIB_PROXY``), allow-listed URLs and pacing all apply.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as config_module
from .common import BookfetchError
from .sources import SOURCES, gutenberg

# Z-Library searches only inside a browser (over CDP), so it is used only when named in --providers.
DEFAULT_PROVIDERS = tuple(name for name in SOURCES if name != "zlib")
KINDS = {
    "ebook": ("annas", "zlib", "gutenberg", "standardebooks", "openlibrary"),
    "paper": ("arxiv",),
}
# File extension -> the source's own format key, for sources that offer one work in several formats.
FORMAT_KEYS = {
    "gutenberg": {"epub": "epub", "azw3": "azw3", "mobi": "mobi", "txt": "txt"},
    "standardebooks": {"epub": "epub", "azw3": "azw3", "kepub": "kepub"},
    "openlibrary": {"epub": "epub", "pdf": "pdf", "txt": "txt"},
}
ORIGINS = {
    "annas": "Anna's Archive",
    "zlib": "Z-Library",
    "gutenberg": "Project Gutenberg",
    "standardebooks": "Standard Ebooks",
    "openlibrary": "Open Library",
    "arxiv": "arXiv",
}
# The MARC language codes some catalogues use -> ISO 639-1.
MARC = {
    "eng": "en",
    "chi": "zh",
    "zho": "zh",
    "jpn": "ja",
    "kor": "ko",
    "fre": "fr",
    "fra": "fr",
    "ger": "de",
    "deu": "de",
    "spa": "es",
    "rus": "ru",
    "ita": "it",
    "por": "pt",
    "dut": "nl",
    "nld": "nl",
    "pol": "pl",
    "lat": "la",
    "ara": "ar",
}
SIZE_RE = re.compile(r"([\d.]+)\s*([KMG]?B)", re.I)
UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
GUTENBERG_COVER = "https://www.gutenberg.org/cache/epub/{n}/pg{n}.cover.medium.jpg"


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def note(message: str) -> None:
    print(f"bookfetch: {message}", file=sys.stderr, flush=True)


def language_code(value: str) -> str:
    """'English [en]', 'en-US', 'eng' -> a language code ('' when there is none)."""
    value = (value or "").strip()
    bracket = re.search(r"\[([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)?)\]", value)
    if bracket:
        value = bracket.group(1)
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*", value):
        return ""
    primary, _, rest = value.partition("-")
    primary = MARC.get(primary.lower(), primary.lower())
    return f"{primary}-{rest}" if rest else primary


def size_bytes(value: str) -> int | None:
    """'7.5 MB' -> bytes (bookfetch prints sizes in binary units)."""
    m = SIZE_RE.fullmatch((value or "").strip())
    return int(float(m.group(1)) * UNITS[m.group(2).upper()]) if m else None


def year_of(value: str) -> int | None:
    m = re.search(r"\d{4}", value or "")
    return int(m.group(0)) if m else None


def base_id(source: str, ident: str) -> str:
    """The work's id without the format suffix a source adds when a format was asked for."""
    keys = set(FORMAT_KEYS.get(source, {}).values())
    head, sep, tail = ident.rpartition(":")
    return head if sep and tail in keys else ident


def result_of(row: dict, kind: str) -> dict:
    """One bookfetch search row in the protocol's shape."""
    source = row["source"]
    ident = base_id(source, row["id"])
    extension = (row.get("extension") or "").lower()
    out = {
        "id": f"{source}:{ident}",
        "kind": kind,
        "title": row.get("title", ""),
        "author": row.get("author", ""),
        "publisher": row.get("publisher", ""),
        "origin": ORIGINS.get(source, source),
        "language": language_code(row.get("language", "")),
    }
    year = year_of(row.get("year", ""))
    if year:
        out["year"] = year
    size = size_bytes(row.get("size", ""))
    extra = row.get("extra") or {}
    if source == "gutenberg":
        out["formats"] = [{"format": f} for f in FORMAT_KEYS["gutenberg"]]
        out["cover"] = GUTENBERG_COVER.format(n=ident)
    elif source == "standardebooks":
        listed = extra.get("formats") or [extension]
        out["formats"] = [
            {"format": standard_format(key), **({"bytes": size} if size and key == "epub" else {})}
            for key in listed
            if standard_format(key)
        ]
    elif extension:
        out["formats"] = [{"format": extension, **({"bytes": size} if size else {})}]
        if size:
            out["bytes"] = size
    return out


def standard_format(key: str) -> str:
    """A Standard Ebooks format key -> its file extension ('advanced' is an epub too, offered as epub)."""
    return {"epub": "epub", "azw3": "azw3", "kepub": "kepub"}.get(key, "")


def split_id(value: str) -> tuple[str, str]:
    source, sep, ident = value.partition(":")
    if not sep or source not in SOURCES or not ident:
        raise BookfetchError(f"not a work id: {value!r} (expected SOURCE:ID)")
    return source, ident


def open_source(cfg, name: str, account: str | None):
    """The first usable account's source object, or an error saying why none is."""
    accounts = cfg.select(name, account)
    if not accounts:
        raise BookfetchError(f"{name}: {SOURCES[name].NOT_CONFIGURED}")
    last = None
    for acct in accounts:
        try:
            return SOURCES[name].Source.from_account(acct, cfg)
        except BookfetchError as e:
            last = e
    raise last


def close(source) -> None:
    closer = getattr(source, "close", None)
    if closer:
        closer()


def cmd_search(a, cfg) -> int:
    names = [n for n in KINDS[a.kind] if n in a.providers]
    year_from, year_to = a.year_from, a.year_to

    def run(name):
        source = None
        try:
            source = open_source(cfg, name, a.account)
            return source.search(a.query, ext=a.format, lang=a.language, limit=a.limit), None
        except BookfetchError as e:
            return None, f"{name}: {e}"
        finally:
            if source is not None:
                close(source)

    with ThreadPoolExecutor(max_workers=max(len(names), 1)) as pool:
        outcomes = list(pool.map(run, names))
    lists, failures = [], []
    for rows, error in outcomes:
        if error:
            failures.append(error)
            note(error)
        else:
            lists.append([result_of(row, a.kind) for row in rows])
    if names and not lists:
        note(failures[-1] if failures else "no source answered")
        return 1
    results = []
    for index in range(max((len(rows) for rows in lists), default=0)):
        for rows in lists:
            if index < len(rows):
                results.append(rows[index])
    results = [
        r
        for r in results
        if (year_from is None or r.get("year", year_from) >= year_from) and (year_to is None or r.get("year", year_to) <= year_to)
    ]
    emit({"results": results})
    return 0


def cmd_info(a, cfg) -> int:
    source_name, ident = split_id(a.id)
    if source_name == "gutenberg":
        # One request: the book's own feed, with every format and its size.
        source = open_source(cfg, "gutenberg", a.account)
        number = base_id("gutenberg", ident)
        info = gutenberg.parse_book(source.http.get(f"{gutenberg.BASE}/ebooks/{number}.opds", accept="application/atom+xml"), number)
        if not info["title"]:
            raise BookfetchError(f"no book {number} on Project Gutenberg")
        formats = []
        for ext, key in FORMAT_KEYS["gutenberg"].items():
            suffix = gutenberg.FORMATS[key][0]
            if suffix in info["sizes"]:
                length = info["sizes"][suffix]
                formats.append({"format": ext, **({"bytes": int(length)} if str(length or "").isdigit() else {})})
        emit(
            {
                "id": a.id,
                "kind": "ebook",
                "title": info["title"],
                "author": info["author"],
                "language": language_code(info["language"]),
                "origin": ORIGINS["gutenberg"],
                "cover": GUTENBERG_COVER.format(n=number),
                "formats": formats or [{"format": "epub"}, {"format": "txt"}],
            }
        )
        return 0
    # The other sources have no cheap detail request; the id alone says what can be fetched.
    keys = FORMAT_KEYS.get(source_name)
    emit(
        {
            "id": a.id,
            "kind": "paper" if source_name == "arxiv" else "ebook",
            "origin": ORIGINS.get(source_name, source_name),
            **({"formats": [{"format": f} for f in keys]} if keys else {}),
        }
    )
    return 0


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_download(a, cfg) -> int:
    source_name, ident = split_id(a.id)
    fmt = (a.format or "").lower()
    keys = FORMAT_KEYS.get(source_name)
    if keys is not None:
        if fmt not in keys:
            raise BookfetchError(f"{source_name} has no {fmt} format (offered: {', '.join(keys)})")
        target = f"{base_id(source_name, ident)}:{keys[fmt]}"
    else:
        target = ident
    out = Path(a.output).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    # Downloaded into a hidden folder first, so a file under its final name in the output folder is always whole.
    scratch = Path(tempfile.mkdtemp(prefix=".bookfetch-", dir=out))
    source = None
    try:
        source = open_source(cfg, source_name, a.account)
        path = Path(source.download(target, out=scratch))
        extension = path.suffix.lower().lstrip(".")
        if keys is None and fmt and extension != fmt:
            raise BookfetchError(f"{source_name}: the file is {extension or 'of no known type'}, not {fmt}")
        final = out / path.name
        shutil.move(str(path), final)
        file = {"name": final.name, "bytes": final.stat().st_size, "sha256": sha256_of(final)}
        emit({"event": "done", **file})
        emit({"event": "result", "folder": str(out), "files": [file]})
        return 0
    finally:
        if source is not None:
            close(source)
        shutil.rmtree(scratch, ignore_errors=True)


def cmd_cover(a, cfg) -> int:
    """Write a cover picture; only URLs a source's own allow list accepts are fetched, through that source's HTTP."""
    m = re.fullmatch(r"https://www\.gutenberg\.org/cache/epub/(\d+)/pg\1\.cover\.medium\.jpg", a.url)
    if not m:
        raise BookfetchError("not a cover address bookfetch knows")
    source = open_source(cfg, "gutenberg", a.account)
    data = source.http.get(a.url, accept="image/*", limit=2 << 20)
    if not data.startswith(b"\xff\xd8\xff"):
        raise BookfetchError("the cover is not a JPEG")
    Path(a.output).write_bytes(data)
    return 0


def provider_list(value: str) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(n.strip() for n in value.split(",") if n.strip()))
    unknown = [n for n in names if n not in SOURCES]
    if not names or unknown:
        raise argparse.ArgumentTypeError(f"expected source names from {', '.join(SOURCES)}")
    return names


def add_parser(sub) -> None:
    sp = sub.add_parser("source", help="speak the media hub source protocol (search, info, download, cover) as JSON")
    sp.add_argument(
        "--providers",
        type=provider_list,
        default=DEFAULT_PROVIDERS,
        help="sources to use, comma-separated (default: all but zlib, whose search needs a browser)",
    )
    sp.add_argument("--account", default=None, help="use only this account name")
    verbs = sp.add_subparsers(dest="verb", required=True)
    s = verbs.add_parser("search")
    s.add_argument("--kind", choices=sorted(KINDS), required=True)
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--language")
    s.add_argument("--format")
    s.add_argument("--year-from", type=int)
    s.add_argument("--year-to", type=int)
    s.add_argument("--json", action="store_true")
    s.add_argument("query")
    i = verbs.add_parser("info")
    i.add_argument("--json", action="store_true")
    i.add_argument("id")
    d = verbs.add_parser("download")
    d.add_argument("--id", required=True)
    d.add_argument("--format", required=True)
    d.add_argument("--output", required=True)
    d.add_argument("--json", action="store_true")
    c = verbs.add_parser("cover")
    c.add_argument("--output", required=True)
    c.add_argument("url")


VERBS = {"search": cmd_search, "info": cmd_info, "download": cmd_download, "cover": cmd_cover}


def run(a: argparse.Namespace, cfg: config_module.Config) -> int:
    if a.verb == "search" and a.offset:
        emit({"results": []})  # one page only: every source answers its best matches at once
        return 0
    try:
        return VERBS[a.verb](a, cfg)
    except BookfetchError as e:
        note(str(e))
        return 1
