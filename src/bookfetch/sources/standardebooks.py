"""Standard Ebooks: search through the open OPDS search feed, downloads from standardebooks.org.

Every page on the site carries a hidden link that bans the visitor's address for
24 hours when followed, so this module never follows links from pages: it only
requests the search feed and download URLs built from a book's id, and the HTTP
helper refuses anything else.

Anonymous downloads are limited per address (35 in 30 seconds, 100 in 6 hours);
bookfetch keeps a small local log and stays under both. Patrons Circle members
can add their account email (HTTP Basic login with an empty password), which
lifts that limit.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path

from .. import feeds
from ..common import Account, BookfetchError, LoginError, MissingConfigError, result
from ..net import Http, HttpError, Rule, human_size
from .base import ANONYMOUS as ANONYMOUS_NAME
from .base import OpenSource

NAME = "standardebooks"
ANONYMOUS = True
DEFAULT_DOMAINS = ["standardebooks.org"]
DOMAINS_ENV = ""
ACCOUNT_FIELDS = ("email",)
# The email is the whole credential (the password is empty), so treat it as a secret.
SECRET_FIELDS = ("email",)
NOT_CONFIGURED = "works without an account"

BASE = "https://standardebooks.org"
INTERVAL = 2.0
MAX_PER_PAGE = 60
# --ext -> (file name suffix, link title in the feed, extension shown)
FORMATS = {
    "epub": (".epub", "Recommended compatible epub", "epub"),
    "advanced": ("_advanced.epub", "Advanced epub", "epub"),
    "kepub": (".kepub.epub", "Kobo Kepub epub", "kepub"),
    "azw3": (".azw3", "Amazon Kindle azw3", "azw3"),
}
ALIASES = {"epub3": "advanced", "advanced-epub": "advanced", "kobo": "kepub", "kindle": "azw3", "mobi": "azw3"}
# Path segments are lowercase words joined by '-'; several translators are joined by '_'.
SLUG = r"[a-z0-9]+(?:[-_][a-z0-9]+)*"
BOOK = rf"{SLUG}(?:/{SLUG})+"
ID_RE = re.compile(rf"({BOOK})(?::([a-z0-9-]+))?")
# Anonymous per-address limits are 35 per 30 s and 100 per 6 h; stay a little under them.
WINDOWS = ((30.0, 30), (6 * 3600.0, 90))

RULES = [
    Rule(r"standardebooks\.org", r"/feeds/opds/all"),
    Rule(r"standardebooks\.org", rf"/ebooks/{BOOK}/downloads/[a-z0-9_-]+(?:_advanced)?(?:\.kepub)?\.(?:epub|azw3)"),
]


class StandardEbooksError(BookfetchError):
    """A Standard Ebooks error."""


class PatronLoginError(StandardEbooksError, LoginError):
    """The configured email is not accepted as a Patrons Circle login."""


def env_account(env: Mapping[str, str] | None = None) -> Account | None:
    env = os.environ if env is None else env
    email = env.get("STANDARDEBOOKS_EMAIL", "").strip()
    return Account(NAME, "env", "environment (STANDARDEBOOKS_EMAIL)", {"email": email}, SECRET_FIELDS) if email else None


def format_key(ext: str | None) -> str | None:
    key = (ext or "epub").lower().lstrip(".")
    key = ALIASES.get(key, key)
    return key if key in FORMATS else None


def parse_id(value: str) -> tuple[str, str]:
    """'jane-austen/pride-and-prejudice[:azw3]' (or the book's URL) -> (path, format key)."""
    value = value.strip()
    if value.startswith(f"{BASE}/ebooks/"):
        value = value[len(f"{BASE}/ebooks/") :].rstrip("/")
    m = ID_RE.fullmatch(value)
    key = format_key(m.group(2)) if m else None
    if not m or not key:
        raise StandardEbooksError(
            f"not a Standard Ebooks id: {value!r} (expected e.g. jane-austen/pride-and-prejudice or jane-austen/emma:azw3)"
        )
    return m.group(1), key


def download_url(path: str, key: str) -> str:
    return f"{BASE}/ebooks/{path}/downloads/{path.replace('/', '_')}{FORMATS[key][0]}?source=download"


def search_url(query: str, limit: int) -> str:
    params = {"query": query.strip(), "per-page": str(min(max(limit, 1), MAX_PER_PAGE))}
    return f"{BASE}/feeds/opds/all?" + urllib.parse.urlencode(params)


def book_path(entry_id: str) -> str:
    prefix = f"{BASE}/ebooks/"
    path = entry_id[len(prefix) :] if entry_id.startswith(prefix) else ""
    return path if re.fullmatch(BOOK, path) else ""


def parse_search(data: bytes, ext: str | None = None) -> list[dict]:
    key = format_key(ext)
    if not key:
        return []
    rows = []
    for entry in feeds.parse(data).entries:
        path = book_path(entry.id)
        if not path:
            continue
        links = {link.title: link for link in entry.acquisitions()}
        link = links.get(FORMATS[key][1])
        if links and not link:
            continue
        rows.append(
            result(
                NAME,
                path if key == "epub" else f"{path}:{key}",
                title=entry.title,
                author=", ".join(entry.authors),
                publisher="Standard Ebooks",
                year=(entry.issued or entry.published)[:4],
                language=entry.language,
                extension=FORMATS[key][2],
                size=human_size(link.length) if link else "",
                extra={"url": f"{BASE}/ebooks/{path}", "formats": sorted(k for k, v in FORMATS.items() if v[1] in links)},
            )
        )
    return rows


class DownloadLog:
    """Times of recent anonymous downloads, kept in a small JSON file so separate runs share it."""

    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.path = path
        self.clock = clock
        self.sleep = sleep

    def _load(self) -> list[float]:
        if not self.path:
            return []
        try:
            data = json.loads(self.path.read_text())
            return [float(t) for t in data if isinstance(t, int | float)]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, times: list[float]) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(times))
        except OSError:
            pass

    def reserve(self) -> None:
        """Wait (or refuse) so this download stays under the anonymous limits, then record it."""
        now = self.clock()
        times = [t for t in self._load() if now - t < WINDOWS[-1][0]]
        long_span, long_max = WINDOWS[-1]
        if len(times) >= long_max:
            wait = int(times[-long_max] + long_span - now) + 1
            raise StandardEbooksError(
                f"anonymous download limit reached ({long_max} in 6 hours from this machine); try again in about {wait // 60 + 1} min, "
                "or add a Patrons Circle email"
            )
        short_span, short_max = WINDOWS[0]
        recent = [t for t in times if now - t < short_span]
        if len(recent) >= short_max:
            self.sleep(recent[-short_max] + short_span - now)
            now = self.clock()
        times.append(now)
        self._save(times)


def state_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_STATE_HOME", "").strip() or os.path.join(env.get("HOME", "").strip() or str(Path.home()), ".local", "state")
    return Path(base) / "bookfetch" / "standardebooks-downloads.json"


class Source(OpenSource):
    name = NAME
    id_help = "AUTHOR/TITLE[:FORMAT]"

    def __init__(self, http: Http | None = None, account: Account | None = None, email: str = "", log: DownloadLog | None = None):
        self.email = email
        super().__init__(http, account)
        self.log = log or DownloadLog(None)
        if email:
            self.downloads_note = "no download limit (Patrons Circle)"

    @property
    def limits(self) -> str:
        if self.email:
            return "Patrons Circle login: no per-address download limit"
        return "anonymous: 35 downloads per 30 s and 100 per 6 h per address; bookfetch stays under both"

    def make_http(self) -> Http:
        headers = {}
        if self.email:
            token = base64.b64encode(f"{self.email}:".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        return Http(NAME, RULES, intervals={"standardebooks.org": INTERVAL}, headers=headers)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        account = env_account(env)
        return cls(email=account.get("email") if account else "", log=DownloadLog(state_path(env)))

    @classmethod
    def from_account(cls, account: Account, config, browser: bool = True):
        log = DownloadLog(state_path(config.env))
        if account.name == ANONYMOUS_NAME and not account.fields:
            return cls(account=account, log=log)
        email = account.get("email")
        if not email:
            raise MissingConfigError(f"{account.label}: no email")
        return cls(account=account, email=email, log=log)

    def quota(self) -> dict:
        return {"account": "Patrons Circle" if self.email else "anonymous", "limits": self.limits}

    def _call(self, fn):
        try:
            return fn()
        except HttpError as e:
            if e.status == 401 and self.email:
                raise PatronLoginError("the email was not accepted as a Patrons Circle login") from None
            if e.status == 429:
                raise StandardEbooksError(
                    "Standard Ebooks rate limit reached for this address (35 per 30 s, 100 per 6 h); try later"
                ) from None
            raise

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict]:
        if not query.strip() or not format_key(ext) or limit <= 0:
            return []
        if lang and not lang.lower().startswith("en"):
            return []
        data = self._call(lambda: self.http.get(search_url(query, limit), accept="application/atom+xml"))
        return parse_search(data, ext)[:limit]

    def download(self, id: str, out: str | Path = ".", name: str | None = None) -> Path:
        path, key = parse_id(id)
        if not self.email:
            self.log.reserve()
        url = download_url(path, key)
        fallback = url.rsplit("/", 1)[1].split("?")[0]
        magic = None if key == "azw3" else b"PK"
        try:
            return self._call(lambda: self.http.download(url, out, name=name, fallback=fallback, magic=magic))
        except HttpError as e:
            if e.status == 404:
                raise StandardEbooksError(f"no {key} file for {path} (check the id as printed by search)") from None
            raise
