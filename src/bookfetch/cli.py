"""Command line interface for bookfetch."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .common import BookfetchError, MissingConfigError
from .sources import SOURCES

DESCRIPTION = """Search and download books from Anna's Archive and Z-Library.

Sources are configured with environment variables:
  annas  ANNAS_KEY or ANNAS_KEY_FILE (member secret key), ANNAS_DOMAINS
  zlib   ZLIB_EMAIL plus ZLIB_PASSWORD or ZLIB_PASSWORD_FILE, ZLIB_DOMAINS,
         BOOKFETCH_CDP_URL (a Chromium with remote debugging, needed for
         Z-Library search)

examples:
  bookfetch search "title author" --ext epub --lang en --limit 5
  bookfetch download annas:MD5 --out DIR [--name FILE]
  bookfetch download zlib:ID/HASH --out DIR
  bookfetch quota
"""

SOURCE_CHOICES = [*SOURCES, "all"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bookfetch", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--annas-key-file", help="file holding the Anna's Archive member secret key")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search", help="search and list candidates")
    sp.add_argument("query")
    sp.add_argument("--source", choices=SOURCE_CHOICES, default="all", help="where to search (default: all configured)")
    sp.add_argument("--ext", help="file type, e.g. epub, pdf")
    sp.add_argument("--lang", help="language code, e.g. en, zh")
    sp.add_argument("--limit", type=int, default=10, help="results per source (default: 10)")
    sp.add_argument("--json", action="store_true", help="print results as JSON")
    dp = sub.add_parser("download", help="download one file (uses one download from the source's daily quota)")
    dp.add_argument("target", metavar="SOURCE:ID", help="as printed by search, e.g. annas:MD5 or zlib:ID/HASH")
    dp.add_argument("--out", default=".", help="directory to save into (default: current)")
    dp.add_argument("--name", help="file name to save as")
    qp = sub.add_parser("quota", help="show download usage for each source")
    qp.add_argument("--source", choices=SOURCE_CHOICES, default="all")
    return p


def make_source(name: str, a: argparse.Namespace):
    module = SOURCES[name]
    if name == "annas":
        return module.Source.from_env(key_file=a.annas_key_file)
    return module.Source.from_env()


def close(source) -> None:
    closer = getattr(source, "close", None)
    if closer:
        closer()


def parse_target(value: str) -> tuple[str, str]:
    name, sep, ident = value.partition(":")
    if not sep or name not in SOURCES or not ident.strip():
        raise argparse.ArgumentTypeError(f"expected SOURCE:ID with SOURCE one of {', '.join(SOURCES)}, got {value!r}")
    return name, ident.strip()


def note(msg: str) -> None:
    print(f"bookfetch: {msg}", file=sys.stderr)


def for_each_source(selected: str, a: argparse.Namespace, action) -> int:
    """Run ``action(name, source)`` for the selected sources.

    With 'all', unconfigured sources are skipped with a note and a failing source
    does not stop the others. Exit code: 0 if any source worked, 1 if all
    configured sources failed, 2 if none was configured.
    """
    names = list(SOURCES) if selected == "all" else [selected]
    ok = failed = 0
    for name in names:
        try:
            source = make_source(name, a)
        except MissingConfigError as e:
            note(f"{name}: skipped, {e}" if selected == "all" else f"{name}: {e}")
            continue
        try:
            action(name, source)
            ok += 1
        except BookfetchError as e:
            note(f"{name}: {e}")
            failed += 1
        finally:
            close(source)
    if ok:
        return 0
    return 1 if failed else 2


def format_result(i: int, r: dict) -> str:
    summary = " · ".join(x for x in (r["language"], r["extension"].upper(), r["size"], r["year"]) if x)
    byline = " | ".join(x for x in (r["author"], r["publisher"]) if x)
    return f"{i}. {r['title']}\n   {byline}\n   {summary}\n   id {r['source']}:{r['id']}"


def cmd_search(a: argparse.Namespace) -> int:
    rows: list[dict] = []

    def action(name, source):
        rows.extend(source.search(a.query, ext=a.ext, lang=a.lang, limit=a.limit))

    code = for_each_source(a.source, a, action)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif code == 0:
        print("\n".join(format_result(i, r) for i, r in enumerate(rows, 1)) if rows else "no results")
    return code


def cmd_download(a: argparse.Namespace) -> int:
    name, ident = parse_target(a.target)
    try:
        source = make_source(name, a)
    except MissingConfigError as e:
        note(f"{name}: {e}")
        return 2
    try:
        path = source.download(ident, out=a.out, name=a.name)
    except MissingConfigError as e:
        note(f"{name}: {e}")
        return 2
    except BookfetchError as e:
        note(f"{name}: {e}")
        return 1
    finally:
        close(source)
    print(f"{path} ({path.stat().st_size} bytes); {name} downloads left today: {source.downloads_left}")
    return 0


def cmd_quota(a: argparse.Namespace) -> int:
    def action(name, source):
        q = source.quota()
        print(f"{name}: " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in q.items()))

    return for_each_source(a.source, a, action)


COMMANDS = {"search": cmd_search, "download": cmd_download, "quota": cmd_quota}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
