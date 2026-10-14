"""Anna's Archive: login, search page parsing, and the member download API.

Search pages sit behind a bot check that lets logged-in members through, so the
client logs in with the secret key first and keeps the session cookie for the run.
Downloads use the member JSON API (/dyn/api/fast_download.json).
"""

from __future__ import annotations

import html
import http.client
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from ..common import (
    DOWNLOAD_TIMEOUT,
    TIMEOUT,
    USER_AGENT,
    Account,
    BookfetchError,
    LoginError,
    MissingConfigError,
    QuotaExhaustedError,
    env_list,
    filename_from_url,
    read_secret_file,
    result,
    safe_filename,
)

NAME = "annas"
# Official domains as listed on Wikipedia and the site's own footer. Override
# with ANNAS_DOMAINS (comma-separated) when they change; check Wikipedia first.
DEFAULT_DOMAINS = ["annas-archive.pk", "annas-archive.gl", "annas-archive.gd"]
DOMAINS_ENV = "ANNAS_DOMAINS"
ACCOUNT_FIELDS = ("key", "key_file")
SECRET_FIELDS = ("key",)
NOT_CONFIGURED = "no key: set ANNAS_KEY, set ANNAS_KEY_FILE, pass --annas-key-file, or add an account to the config file"
RESULT_SPLIT = '<div class="flex  pt-3 pb-3 border-b'
MD5_RE = re.compile(r"[0-9a-f]{32}")


class AnnasError(BookfetchError):
    """An Anna's Archive site or API error."""


class MissingKeyError(MissingConfigError):
    """The secret key could not be loaded."""


class AnnasLoginError(AnnasError, LoginError):
    """The key was rejected or no domain was reachable."""


class NoDownloadsLeftError(AnnasError, QuotaExhaustedError):
    """The account has used today's fast downloads."""


def env_account(env: Mapping[str, str] | None = None, key_file: str | None = None) -> Account | None:
    """The account defined by ANNAS_KEY / ANNAS_KEY_FILE, or by the --annas-key-file flag."""
    env = os.environ if env is None else env
    if key_file:
        return Account(NAME, "env", "--annas-key-file", {"key_file": key_file}, SECRET_FIELDS)
    fields = {"key": env.get("ANNAS_KEY", "").strip(), "key_file": env.get("ANNAS_KEY_FILE", "").strip()}
    if not any(fields.values()):
        return None
    names = " and ".join(var for var, k in (("ANNAS_KEY", "key"), ("ANNAS_KEY_FILE", "key_file")) if fields[k])
    return Account(NAME, "env", f"environment ({names})", {k: v for k, v in fields.items() if v}, SECRET_FIELDS)


def account_key(account: Account) -> str:
    try:
        key = account.secret("key", "key")
    except MissingConfigError as e:
        raise MissingKeyError(str(e)) from None
    if not key:
        raise MissingKeyError(f"{account.label}: no key (set key or key_file)")
    return key


def load_key(key_file: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Return the member secret key.

    Precedence: an explicit ``key_file`` (the --annas-key-file flag), then the
    ANNAS_KEY environment variable, then the file named by ANNAS_KEY_FILE.
    """
    env = os.environ if env is None else env
    if key_file:
        return _read_key_file(key_file)
    key = env.get("ANNAS_KEY", "").strip()
    if key:
        return key
    path = env.get("ANNAS_KEY_FILE", "").strip()
    if path:
        return _read_key_file(path)
    raise MissingKeyError("no key: set ANNAS_KEY, set ANNAS_KEY_FILE, or pass --annas-key-file")


def _read_key_file(path: str) -> str:
    try:
        return read_secret_file(path, "key")
    except MissingConfigError as e:
        raise MissingKeyError(str(e)) from None


def domains(env: Mapping[str, str] | None = None) -> list[str]:
    return env_list("ANNAS_DOMAINS", DEFAULT_DOMAINS, env)


def clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_results(page: str) -> list[dict[str, str]]:
    """Parse result blocks from a search page into dicts."""
    results = []
    for block in page.split(RESULT_SPLIT)[1:]:
        md5 = re.search(r'href="/md5/([0-9a-f]{32})"', block)
        title = re.search(r'<a href="/md5/[0-9a-f]+" class="line-clamp-\[3\][^>]*>(.*?)</a>', block, re.S)
        if not (md5 and title):
            continue
        author = re.search(r"icon-\[mdi--user-edit\][^<]*</span>(.*?)</a>", block, re.S)
        publisher = re.search(r"icon-\[mdi--company\][^<]*</span>(.*?)</a>", block, re.S)
        info = re.search(r"text-gray-800[^>]*>(.*?)<a ", block, re.S)
        results.append(
            {
                "md5": md5.group(1),
                "title": clean(title.group(1)),
                "author": clean(author.group(1)) if author else "",
                "publisher": clean(publisher.group(1)) if publisher else "",
                "info": re.sub(r"\s*·\s*$", "", clean(info.group(1)).replace("\xa0", " ")) if info else "",
            }
        )
    return results


def parse_quota(page: str) -> dict[str, str]:
    """Read membership and fast download usage from the /account/ page."""
    m = re.search(r"Membership:\s*(.*?)</", page, re.S)
    u = re.search(r"Fast downloads used[^:]*:\s*(.*?)<a ", page, re.S)
    return {
        "membership": clean(m.group(1)) if m else "?",
        "fast_downloads_used": clean(u.group(1)) if u else "?",
    }


class Session:
    def __repr__(self) -> str:  # never show the key
        return f"Session(base={self.base!r})"

    def __init__(self, key: str, opener: urllib.request.OpenerDirector | None = None, domain_list: list[str] | None = None):
        self.key = key
        self.jar = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.domains = domain_list if domain_list is not None else domains()
        self.base: str | None = None

    def _open(self, url: str, data: bytes | None = None, timeout: int = TIMEOUT):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        return self.opener.open(req, timeout=timeout)

    def _logged_in(self) -> bool:
        return any(c.name.startswith("aa_account_id") for c in self.jar)

    def login(self) -> None:
        """Log in on the first reachable official domain."""
        errors = []
        for d in self.domains:
            try:
                self._open(f"https://{d}/account/", urllib.parse.urlencode({"key": self.key}).encode())
                if self._logged_in():
                    self.base = f"https://{d}"
                    return
                errors.append(f"{d}: key rejected")
            except (OSError, http.client.HTTPException) as e:  # network or HTTP error; try the next domain
                errors.append(f"{d}: {e}")
        raise AnnasLoginError("login failed: " + "; ".join(errors))

    def get(self, path: str) -> str:
        if not self.base:
            self.login()
        try:
            return self._open(self.base + path).read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise AnnasError(f"GET {path.split('?')[0]} failed: HTTP {e.code} (search needs an active membership)") from None
        except (OSError, http.client.HTTPException) as e:
            raise AnnasError(f"GET {path.split('?')[0]} failed: {e}") from None

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict[str, str]]:
        params = {"q": query}
        if ext:
            params["ext"] = ext
        if lang:
            params["lang"] = lang
        return parse_results(self.get("/search?" + urllib.parse.urlencode(params)))[:limit]

    def quota(self) -> dict[str, str]:
        return parse_quota(self.get("/account/"))

    def fast_download_info(self, md5: str) -> dict:
        """Call the member download API. Every call counts against the daily quota."""
        if not self.base:
            self.login()
        url = f"{self.base}/dyn/api/fast_download.json?" + urllib.parse.urlencode({"md5": md5, "key": self.key})
        try:
            body = self._open(url).read()
        except urllib.error.HTTPError as e:
            body = e.read()
        except (OSError, http.client.HTTPException) as e:
            raise AnnasError(f"API request failed: {e}") from None
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            raise AnnasError("API error: response was not JSON") from None
        if not isinstance(data, dict) or not data.get("download_url"):
            error = data.get("error") if isinstance(data, dict) else None
            if error in ("Invalid secret key", "Not a member"):
                raise AnnasLoginError(f"API error: {error}")
            if error and "no downloads left" in str(error).lower():
                raise NoDownloadsLeftError(f"API error: {error}")
            raise AnnasError(f"API error: {error or 'no download_url'}")
        return data

    def download(self, md5: str, out: str | Path = ".", name: str | None = None) -> tuple[Path, dict]:
        data = self.fast_download_info(md5)
        url = data["download_url"]
        filename = safe_filename(name or filename_from_url(url) or md5, fallback=md5)
        out_dir = Path(out).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        try:
            with self._open(url, timeout=DOWNLOAD_TIMEOUT) as r, open(path, "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
        except (OSError, http.client.HTTPException) as e:
            raise AnnasError(f"download failed: {e}") from None
        return path, data


def split_info(info: str) -> dict[str, str]:
    """Pull language, extension, size and year out of a result's summary line."""
    out = {"language": "", "extension": "", "size": "", "year": ""}
    for part in (p.strip() for p in info.split("·")):
        if not out["language"] and re.fullmatch(r".+\[[a-z]{2,3}(-[A-Za-z]+)?\]", part):
            out["language"] = part
        elif not out["extension"] and re.fullmatch(r"[A-Z0-9]{2,5}", part):
            out["extension"] = part.lower()
        elif not out["size"] and re.fullmatch(r"[\d.]+\s?[KMG]B", part):
            out["size"] = part
        elif not out["year"] and re.fullmatch(r"\d{4}", part):
            out["year"] = part
    return out


class Source:
    """Anna's Archive in the common source shape."""

    name = NAME
    id_help = "MD5"

    def __init__(self, session: Session):
        self.session = session
        self.downloads_left = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, key_file: str | None = None) -> Source:
        return cls(Session(load_key(key_file, env), domain_list=domains(env)))

    @classmethod
    def from_account(cls, account: Account, config, browser: bool = True) -> Source:
        """``config`` supplies ``domains(source)`` (see bookfetch.config.Config)."""
        return cls(Session(account_key(account), domain_list=config.domains(NAME)))

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict]:
        rows = self.session.search(query, ext=ext, lang=lang, limit=limit)
        return [
            result(
                NAME,
                r["md5"],
                title=r["title"],
                author=r["author"],
                publisher=r["publisher"],
                **split_info(r["info"]),
                extra={"info": r["info"]},
            )
            for r in rows
        ]

    def download(self, id: str, out: str | Path = ".", name: str | None = None) -> Path:
        md5 = id.strip().lower()
        if not MD5_RE.fullmatch(md5):
            raise AnnasError(f"not an MD5: {id!r}")
        path, data = self.session.download(md5, out=out, name=name)
        self.downloads_left = (data.get("account_fast_download_info") or {}).get("downloads_left")
        return path

    def quota(self) -> dict:
        return self.session.quota()
