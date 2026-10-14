"""Command line interface for bookfetch."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from . import __version__, config
from .common import Account, BookfetchError, ConfigError, LoginError, MissingConfigError, QuotaExhaustedError
from .sources import SOURCES

DESCRIPTION = """Search and download books from Anna's Archive, Z-Library, Project Gutenberg,
Standard Ebooks, Open Library (Internet Archive) and arXiv.

Project Gutenberg, Standard Ebooks, Open Library and arXiv work without an
account (built-in account 'anonymous'). Anna's Archive and Z-Library need one.
Accounts come from environment variables (one account per source, named 'env')
and from an optional TOML config file with any number of named accounts:
  annas  ANNAS_KEY or ANNAS_KEY_FILE (member secret key), ANNAS_DOMAINS
  zlib   ZLIB_EMAIL plus ZLIB_PASSWORD or ZLIB_PASSWORD_FILE, ZLIB_DOMAINS,
         BOOKFETCH_CDP_URL (a Chromium with remote debugging, needed for
         Z-Library search)
  standardebooks  STANDARDEBOOKS_EMAIL (optional Patrons Circle email)
  config --config PATH, else BOOKFETCH_CONFIG, else
         $XDG_CONFIG_HOME/bookfetch/config.toml (~/.config/bookfetch/config.toml)

examples:
  bookfetch search "title author" --ext epub --lang en --limit 5
  bookfetch download annas:MD5 --out DIR [--name FILE] [--account NAME]
  bookfetch download zlib:ID/HASH --out DIR
  bookfetch download gutenberg:1342 --out DIR
  bookfetch download standardebooks:jane-austen/emma --out DIR
  bookfetch download openlibrary:IDENTIFIER --out DIR
  bookfetch download arxiv:1706.03762 --out DIR
  bookfetch quota
  bookfetch accounts
"""

DEFAULT_LIMIT_ONE = 10
DEFAULT_LIMIT_ALL = 5


def add_common(p: argparse.ArgumentParser, top: bool) -> None:
    """--config and --account work before or after the command name."""
    default = None if top else argparse.SUPPRESS
    p.add_argument("--config", metavar="PATH", default=default, help="TOML config file with accounts")
    p.add_argument("--account", metavar="NAME", default=default, help="use only this account (default: the first, then the next ones)")


def build_parser() -> argparse.ArgumentParser:
    choices = [*SOURCES, "all"]
    p = argparse.ArgumentParser(prog="bookfetch", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--annas-key-file", help="file holding the Anna's Archive member secret key (replaces the 'env' account)")
    add_common(p, top=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search", help="search and list candidates")
    sp.add_argument("query")
    sp.add_argument("--source", choices=choices, default="all", help="where to search (default: all configured)")
    sp.add_argument("--ext", help="file type, e.g. epub, pdf")
    sp.add_argument("--lang", help="language code, e.g. en, zh")
    sp.add_argument(
        "--limit", type=int, help=f"results per source (default: {DEFAULT_LIMIT_ALL} with all sources, {DEFAULT_LIMIT_ONE} with one)"
    )
    sp.add_argument("--json", action="store_true", help="print results as JSON")
    add_common(sp, top=False)
    dp = sub.add_parser(
        "download", help="download one file (with Anna's Archive and Z-Library this uses one download from the daily quota)"
    )
    dp.add_argument("target", metavar="SOURCE:ID", help="as printed by search, e.g. annas:MD5, zlib:ID/HASH, gutenberg:1342")
    dp.add_argument("--out", default=".", help="directory to save into (default: current)")
    dp.add_argument("--name", help="file name to save as")
    add_common(dp, top=False)
    qp = sub.add_parser("quota", help="show download usage for each account")
    qp.add_argument("--source", choices=choices, default="all")
    add_common(qp, top=False)
    ap = sub.add_parser("accounts", aliases=["config"], help="list configured accounts and where they come from (no secrets)")
    ap.add_argument("--source", choices=choices, default="all")
    add_common(ap, top=False)
    return p


def load_config(a: argparse.Namespace) -> config.Config:
    overrides = {}
    if a.annas_key_file:
        overrides["annas"] = SOURCES["annas"].env_account(key_file=a.annas_key_file)
    return config.load(a.config, overrides=overrides, warn=note)


def make_source(name: str, account: Account, cfg: config.Config, browser: bool = True):
    return SOURCES[name].Source.from_account(account, cfg, browser=browser)


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


def source_names(selected: str) -> list[str]:
    return list(SOURCES) if selected == "all" else [selected]


def accounts_for(cfg: config.Config, names: list[str], wanted: str | None) -> dict[str, list[Account]]:
    """The accounts to use per source; with ``wanted``, only accounts of that name.

    Raises ConfigError if ``wanted`` matches no account in any of ``names``.
    """
    chosen = {name: cfg.select(name, wanted) for name in names}
    if wanted and not any(chosen.values()):
        known = ", ".join(a.label for name in names for a in cfg.accounts[name]) or "none"
        where = names[0] if len(names) == 1 else " or ".join(names)
        raise ConfigError(f"no account named {wanted!r} for {where} (configured: {known})")
    return chosen


def not_configured(name: str, wanted: str | None) -> str:
    return f"no account named {wanted!r}" if wanted else SOURCES[name].NOT_CONFIGURED


class AccountFailed(Exception):
    """The account that failed last, and its error."""

    def __init__(self, account: Account, error: BookfetchError, several: bool):
        self.account, self.error, self.several = account, error, several

    def message(self, name: str) -> str:
        return f"{self.account.label if self.several else name}: {self.error}"


def run_with_fallback(name: str, accounts: list[Account], cfg, action, fallback_on, browser: bool = True):
    """Try ``action(source)`` with each account in turn.

    Moves to the next account only on the errors in ``fallback_on`` and says so on
    stderr; every account is tried at most once. Returns (account, source, value);
    raises AccountFailed with the last error when no account worked.
    """
    for i, account in enumerate(accounts):
        source = None
        try:
            source = make_source(name, account, cfg, browser)
            return account, source, action(source)
        except BookfetchError as e:
            if not isinstance(e, fallback_on) or i + 1 == len(accounts):
                raise AccountFailed(account, e, len(cfg.accounts[name]) > 1) from None
            note(f"{account.label}: {e}; trying {accounts[i + 1].label}")
        finally:
            if source is not None:
                close(source)
    raise AssertionError("no accounts")


def for_each_source(selected: str, a: argparse.Namespace, cfg, action, fallback_on=(MissingConfigError, LoginError)) -> tuple[int, dict]:
    """Run ``action(source)`` for the selected sources, one account each.

    With several sources they run in parallel threads, so a slow source does not
    hold up the others; results come back per source name, in source order.
    Unconfigured sources are skipped with a note and a failing source does not
    stop the others. Exit code: 0 if any source worked, 1 if all configured
    sources failed, 2 if none was configured.
    """
    names = source_names(selected)
    chosen = accounts_for(cfg, names, a.account)
    active = []
    for name in names:
        if chosen[name]:
            active.append(name)
        else:
            reason = not_configured(name, a.account)
            note(f"{name}: skipped, {reason}" if selected == "all" else f"{name}: {reason}")

    def run(name):
        try:
            return run_with_fallback(name, chosen[name], cfg, action, fallback_on)[2], None
        except AccountFailed as f:
            return None, f

    if len(active) > 1:
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            outcomes = dict(zip(active, pool.map(run, active), strict=True))
    else:
        outcomes = {name: run(name) for name in active}
    ok = failed = 0
    results = {}
    for name in active:
        value, f = outcomes[name]
        if f is None:
            results[name] = value
            ok += 1
            continue
        note(f.message(name))
        # A lone account that is merely unconfigured counts as "not configured".
        if not (isinstance(f.error, MissingConfigError) and len(chosen[name]) == 1):
            failed += 1
    if ok:
        return 0, results
    return (1 if failed else 2), results


def format_result(i: int, r: dict) -> str:
    summary = " · ".join(x for x in (r["language"], r["extension"].upper(), r["size"], r["year"]) if x)
    byline = " | ".join(x for x in (r["author"], r["publisher"]) if x)
    return f"{i}. {r['title']}\n   {byline}\n   {summary}\n   id {r['source']}:{r['id']}"


def cmd_search(a: argparse.Namespace, cfg) -> int:
    limit = a.limit if a.limit is not None else DEFAULT_LIMIT_ALL if a.source == "all" else DEFAULT_LIMIT_ONE
    code, results = for_each_source(a.source, a, cfg, lambda source: source.search(a.query, ext=a.ext, lang=a.lang, limit=limit))
    rows = [r for name in results for r in results[name]]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif code == 0:
        print("\n".join(format_result(i, r) for i, r in enumerate(rows, 1)) if rows else "no results")
    return code


def cmd_download(a: argparse.Namespace, cfg) -> int:
    name, ident = parse_target(a.target)
    accounts = accounts_for(cfg, [name], a.account)[name]
    if not accounts:
        note(f"{name}: {not_configured(name, a.account)}")
        return 2
    # Without --account, an account that cannot log in or has no downloads left
    # hands over to the next one. Any other error may already have spent a
    # download, so it stops here.
    fallback = (MissingConfigError, LoginError, QuotaExhaustedError)
    try:
        account, source, path = run_with_fallback(name, accounts, cfg, lambda s: s.download(ident, out=a.out, name=a.name), fallback)
    except AccountFailed as f:
        note(f.message(name))
        return 2 if isinstance(f.error, MissingConfigError) and len(accounts) == 1 else 1
    label = account.label if len(cfg.accounts[name]) > 1 else name
    extra = getattr(source, "downloads_note", None)
    left = f"{label}: {extra}" if extra else f"{label} downloads left today: {source.downloads_left}"
    print(f"{path} ({path.stat().st_size} bytes); {left}")
    return 0


def cmd_quota(a: argparse.Namespace, cfg) -> int:
    names = source_names(a.source)
    chosen = accounts_for(cfg, names, a.account)
    ok = failed = 0
    for name in names:
        if not chosen[name]:
            reason = not_configured(name, a.account)
            note(f"{name}: skipped, {reason}" if a.source == "all" else f"{name}: {reason}")
            continue
        for account in chosen[name]:
            source = None
            try:
                # Plain HTTP is enough for the profile and keeps the browser's
                # own Z-Library session untouched when listing several accounts.
                source = make_source(name, account, cfg, browser=False)
                q = source.quota()
                print(f"{account.label}: " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in q.items()))
                ok += 1
            except BookfetchError as e:
                print(f"{account.label}: error: {e}")
                failed += 1
            finally:
                if source is not None:
                    close(source)
    if ok:
        return 0
    return 1 if failed else 2


def cmd_accounts(a: argparse.Namespace, cfg) -> int:
    state = "" if cfg.found else " (not found; optional)"
    print(f"config file: {cfg.path}{state}")
    names = source_names(a.source)
    chosen = accounts_for(cfg, names, a.account)
    for name in names:
        print(f"{name}: domains {', '.join(cfg.domains(name))}")
        if not chosen[name]:
            print(f"  {'no account named ' + repr(a.account) if a.account else 'no accounts'}")
        for account in chosen[name]:
            if account.name == config.ANONYMOUS_ACCOUNT:
                print(f"  {account.name}: built in; no login")
            else:
                print(f"  {account.name}: from {account.origin}; {account.describe()}")
    return 0


COMMANDS = {"search": cmd_search, "download": cmd_download, "quota": cmd_quota, "accounts": cmd_accounts, "config": cmd_accounts}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        cfg = load_config(a)
        return COMMANDS[a.cmd](a, cfg)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))
    except ConfigError as e:
        note(str(e))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
