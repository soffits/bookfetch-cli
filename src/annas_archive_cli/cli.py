"""Command line interface for annas-archive-cli."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .client import AnnasError, MissingKeyError, Session, load_key

DESCRIPTION = """Search and download from Anna's Archive with a member secret key.

Search and download both need an active membership. The key is read from
--key-file, then ANNAS_KEY, then the file named by ANNAS_KEY_FILE.

examples:
  annas search "title author" --ext epub --lang en --limit 10
  annas download MD5 --out DIR [--name FILE]
  annas quota
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="annas", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--key-file", help="file holding the member secret key")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search", help="search and list candidates")
    sp.add_argument("query")
    sp.add_argument("--ext", help="file type, e.g. epub, pdf")
    sp.add_argument("--lang", help="language code, e.g. en, zh")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--json", action="store_true", help="print results as JSON")
    dp = sub.add_parser("download", help="download one file by MD5 (uses one fast download)")
    dp.add_argument("md5")
    dp.add_argument("--out", default=".", help="directory to save into (default: current)")
    dp.add_argument("--name", help="file name to save as")
    sub.add_parser("quota", help="show membership and fast downloads used")
    return p


def cmd_search(s: Session, a: argparse.Namespace) -> None:
    rows = s.search(a.query, ext=a.ext, lang=a.lang, limit=a.limit)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    if not rows:
        print("no results")
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['title']}\n   {r['author']} | {r['publisher']}\n   {r['info']}\n   md5 {r['md5']}")


def cmd_download(s: Session, a: argparse.Namespace) -> None:
    path, data = s.download(a.md5, out=a.out, name=a.name)
    info = data.get("account_fast_download_info") or {}
    print(f"{path} ({path.stat().st_size} bytes); downloads left today: {info.get('downloads_left')}")


def cmd_quota(s: Session, a: argparse.Namespace) -> None:
    q = s.quota()
    print("membership:", q["membership"])
    print("fast downloads used:", q["fast_downloads_used"])


COMMANDS = {"search": cmd_search, "download": cmd_download, "quota": cmd_quota}


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        s = Session(load_key(a.key_file))
        COMMANDS[a.cmd](s, a)
    except MissingKeyError as e:
        print(f"annas: {e}", file=sys.stderr)
        return 2
    except AnnasError as e:
        print(f"annas: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
