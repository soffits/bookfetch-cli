"""Z-Library through its JSON API (the "eapi").

Login and the profile work over plain HTTP, but search and file lookups sit
behind a browser check that is bound to a real browser. For those, the browser
transport runs the same API calls with ``fetch`` inside a tab of a running
Chromium, reached over the DevTools protocol (BOOKFETCH_CDP_URL).
"""

from __future__ import annotations

import base64
import http.client
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from ..cdp import Browser, CDPError, Page
from ..common import (
    DOWNLOAD_TIMEOUT,
    TIMEOUT,
    USER_AGENT,
    BookfetchError,
    MissingConfigError,
    env_list,
    filename_from_disposition,
    filename_from_url,
    read_secret_file,
    result,
    safe_filename,
)

NAME = "zlib"
# The official domain per Wikipedia's Z-Library article, which warns about many
# fraudulent copies. Override with ZLIB_DOMAINS (comma-separated).
DEFAULT_DOMAINS = ["z-lib.sk"]
ID_RE = re.compile(r"(\d+)/([0-9A-Za-z]+)")
BOT_WALL = (
    "Z-Library's browser check blocked the request; search and downloads need the browser transport: "
    "start Chromium with --remote-debugging-port, log in to Z-Library there once, and set BOOKFETCH_CDP_URL"
)
LANGUAGES = {
    "en": "english", "zh": "chinese", "es": "spanish", "fr": "french", "de": "german", "it": "italian",
    "pt": "portuguese", "ru": "russian", "ja": "japanese", "ko": "korean", "ar": "arabic", "nl": "dutch",
    "pl": "polish", "tr": "turkish", "uk": "ukrainian", "sv": "swedish", "hi": "hindi", "fa": "persian",
}  # fmt: skip


class ZlibError(BookfetchError):
    """A Z-Library site or API error."""


class Credentials:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password

    def __repr__(self) -> str:  # never show the password
        return f"Credentials(email={self.email!r})"


def load_credentials(env: Mapping[str, str] | None = None) -> Credentials | None:
    """ZLIB_EMAIL plus ZLIB_PASSWORD (or a file named by ZLIB_PASSWORD_FILE); None if unset."""
    env = os.environ if env is None else env
    email = env.get("ZLIB_EMAIL", "").strip()
    password = env.get("ZLIB_PASSWORD", "")
    path = env.get("ZLIB_PASSWORD_FILE", "").strip()
    if not email and not password and not path:
        return None
    if not email:
        raise MissingConfigError("ZLIB_EMAIL is not set")
    if not password and path:
        password = read_secret_file(path, "password")
    if not password:
        raise MissingConfigError("no password: set ZLIB_PASSWORD or ZLIB_PASSWORD_FILE")
    return Credentials(email, password)


def domains(env: Mapping[str, str] | None = None) -> list[str]:
    return env_list("ZLIB_DOMAINS", DEFAULT_DOMAINS, env)


def parse_id(value: str) -> tuple[str, str]:
    m = ID_RE.fullmatch(value.strip())
    if not m:
        raise ZlibError(f"not a Z-Library id (expected ID/HASH as printed by search): {value!r}")
    return m.group(1), m.group(2)


def language_name(lang: str) -> str:
    lang = lang.strip().lower()
    return LANGUAGES.get(lang, lang)


def search_form(query: str, ext: str | None, lang: str | None, limit: int) -> list[tuple[str, str]]:
    form = [("message", query), ("limit", str(limit))]
    if ext:
        form.append(("extensions[]", ext.lower()))
    if lang:
        form.append(("languages[]", language_name(lang)))
    return form


def to_result(book: dict) -> dict:
    extra = {k: book[k] for k in ("md5", "filesize", "identifier", "series", "pages", "edition") if book.get(k)}
    return result(
        NAME,
        f"{book.get('id')}/{book.get('hash')}",
        title=book.get("title", ""),
        author=book.get("author", ""),
        publisher=book.get("publisher", ""),
        year=book.get("year") or "",
        language=book.get("language", ""),
        extension=book.get("extension", ""),
        size=book.get("filesizeString", ""),
        extra=extra,
    )


def is_bot_wall(status: int, text: str) -> bool:
    return status in (307, 513) or "DiamWall" in text or "Verifying your browser" in text


def decode(status: int, text: str, what: str) -> dict:
    if is_bot_wall(status, text):
        raise ZlibError(f"{what}: {BOT_WALL}")
    try:
        data = json.loads(text)
    except ValueError:
        raise ZlibError(f"{what}: HTTP {status}, response was not JSON") from None
    if not isinstance(data, dict):
        raise ZlibError(f"{what}: unexpected response")
    if data.get("success") != 1:
        raise ZlibError(f"{what}: {data.get('error') or data.get('message') or f'HTTP {status}, request failed'}")
    return data


class HttpTransport:
    """Plain HTTP with a cookie jar. Login and profile work; search hits the browser check."""

    browser = False

    def __init__(self, domain: str, opener: urllib.request.OpenerDirector | None = None):
        self.base = f"https://{domain}"
        self.jar = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, form: list[tuple[str, str]] | None = None) -> tuple[int, str]:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {"User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException) as e:
            raise ZlibError(f"{method} {path} failed: {e}") from None

    def fetch_file(self, url: str) -> bytes:
        raise ZlibError(f"download blocked: {BOT_WALL}")

    def close(self) -> None:
        pass


FETCH_JS = """(async () => {
  const a = %s;
  const init = {method: a.method, credentials: 'include'};
  if (a.body !== null) {
    init.headers = {'Content-Type': 'application/x-www-form-urlencoded'};
    init.body = a.body;
  }
  const r = await fetch(a.path, init);
  return {status: r.status, text: await r.text()};
})()"""

FETCH_FILE_JS = """(async () => {
  const r = await fetch(%s, {credentials: 'include'});
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return {status: r.status, type: r.headers.get('content-type') || '', b64: btoa(s)};
})()"""


class BrowserTransport:
    """Runs eapi calls with fetch() inside a tab of a running Chromium.

    It opens its own tab on the Z-Library domain and closes it again; other tabs
    are left alone.
    """

    browser = True

    def __init__(self, endpoint: str, domain: str, browser: Browser | None = None, wait: float = 60):
        self.domain = domain
        self.browser_api = browser or Browser(endpoint)
        self.wait = wait
        self.target: dict | None = None
        self.page: Page | None = None

    def _open(self) -> Page:
        if self.page:
            return self.page
        self.target = self.browser_api.new_tab(f"https://{self.domain}/")
        self.page = self.browser_api.attach(self.target)
        deadline = time.monotonic() + self.wait
        while True:
            state = self.page.evaluate("({host: location.host, ready: document.readyState, title: document.title})") or {}
            if state.get("host") == self.domain and state.get("ready") != "loading" and "Verifying" not in state.get("title", ""):
                return self.page
            if time.monotonic() > deadline:
                raise ZlibError(f"the browser tab did not finish loading {self.domain} (title: {state.get('title', '')!r})")
            time.sleep(0.5)

    def request(self, method: str, path: str, form: list[tuple[str, str]] | None = None) -> tuple[int, str]:
        args = {"method": method, "path": path, "body": urllib.parse.urlencode(form) if form is not None else None}
        value = self._open().evaluate(FETCH_JS % json.dumps(args)) or {}
        return int(value.get("status", 0)), value.get("text", "")

    def fetch_file(self, url: str) -> bytes:
        value = self._open().evaluate(FETCH_FILE_JS % json.dumps(url)) or {}
        if value.get("status") != 200 or "text/html" in value.get("type", ""):
            raise ZlibError(f"download failed in the browser: HTTP {value.get('status')}")
        return base64.b64decode(value.get("b64", ""))

    def close(self) -> None:
        if self.page:
            self.page.ws.close()
            self.page = None
        if self.target:
            self.browser_api.close_tab(self.target["id"])
            self.target = None


class Client:
    def __init__(self, transport, credentials: Credentials | None, opener: urllib.request.OpenerDirector | None = None):
        self.transport = transport
        self.credentials = credentials
        self.opener = opener or urllib.request.build_opener()
        self.logged_in = False

    def call(self, method: str, path: str, form: list[tuple[str, str]] | None = None, what: str | None = None) -> dict:
        try:
            status, text = self.transport.request(method, path, form)
        except CDPError as e:
            raise ZlibError(f"browser transport: {e}") from None
        return decode(status, text, what or path)

    def login(self) -> None:
        """Make sure the session is logged in; log in with the credentials if not."""
        if self.logged_in:
            return
        if self.transport.browser:
            try:
                self.call("GET", "/eapi/user/profile")
                self.logged_in = True
                return
            except ZlibError:
                pass  # not logged in in that browser yet
        if not self.credentials:
            raise MissingConfigError("not logged in to Z-Library: set ZLIB_EMAIL and ZLIB_PASSWORD or ZLIB_PASSWORD_FILE")
        form = [("email", self.credentials.email), ("password", self.credentials.password)]
        self.call("POST", "/eapi/user/login", form, what="login")
        self.logged_in = True

    def profile(self) -> dict:
        self.login()
        return self.call("GET", "/eapi/user/profile", what="profile").get("user") or {}

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict]:
        self.login()
        data = self.call("POST", "/eapi/book/search", search_form(query, ext, lang, limit), what="search")
        return list(data.get("books") or [])[:limit]

    def file_info(self, book_id: str, book_hash: str) -> dict:
        self.login()
        info = self.call("GET", f"/eapi/book/{book_id}/{book_hash}/file", what="file lookup").get("file") or {}
        if not info.get("downloadLink"):
            raise ZlibError("file lookup: no download link (daily limit reached or file unavailable)")
        return info

    def fetch(self, url: str) -> tuple[bytes, str]:
        """Fetch the file over plain HTTP; fall back to the browser if that is blocked."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self.opener.open(req, timeout=DOWNLOAD_TIMEOUT) as r:
                if "text/html" not in (r.headers.get("Content-Type") or ""):
                    return r.read(), filename_from_disposition(r.headers.get("Content-Disposition"))
        except urllib.error.HTTPError:
            pass
        except (OSError, http.client.HTTPException) as e:
            if not self.transport.browser:
                raise ZlibError(f"download failed: {e}") from None
        try:
            return self.transport.fetch_file(url), ""
        except CDPError as e:
            raise ZlibError(f"download failed in the browser: {e}") from None


def tidy_name(name: str) -> str:
    """Drop the '(z-library.sk, 1lib.sk, ...)' tag the site adds to file names."""
    return re.sub(r"\s*\((?:[a-z0-9-]+\.[a-z]+(?:,\s*)?)+\)(?=\.[A-Za-z0-9]+$)", "", name).replace("  ", " ")


class Source:
    """Z-Library in the common source shape."""

    name = NAME
    id_help = "ID/HASH"

    def __init__(self, client: Client):
        self.client = client
        self.downloads_left = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Source:
        env = os.environ if env is None else env
        credentials = load_credentials(env)
        cdp = env.get("BOOKFETCH_CDP_URL", "").strip()
        if not credentials and not cdp:
            raise MissingConfigError("not configured: set ZLIB_EMAIL and ZLIB_PASSWORD or ZLIB_PASSWORD_FILE")
        domain = domains(env)[0]
        transport = BrowserTransport(cdp, domain) if cdp else HttpTransport(domain)
        return cls(Client(transport, credentials))

    def search(self, query: str, ext: str | None = None, lang: str | None = None, limit: int = 10) -> list[dict]:
        return [to_result(b) for b in self.client.search(query, ext=ext, lang=lang, limit=limit)]

    def download(self, id: str, out: str | Path = ".", name: str | None = None) -> Path:
        book_id, book_hash = parse_id(id)
        info = self.client.file_info(book_id, book_hash)
        data, served_name = self.client.fetch(info["downloadLink"])
        fallback = f"{book_id}.{info.get('extension') or 'bin'}"
        filename = safe_filename(name or tidy_name(served_name or filename_from_url(info["downloadLink"])), fallback=fallback)
        out_dir = Path(out).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        path.write_bytes(data)
        try:
            user = self.client.profile()
            self.downloads_left = int(user["downloads_limit"]) - int(user["downloads_today"])
        except (BookfetchError, KeyError, TypeError, ValueError):
            self.downloads_left = None
        return path

    def quota(self) -> dict:
        user = self.client.profile()
        return {"downloads_today": user.get("downloads_today"), "downloads_limit": user.get("downloads_limit")}

    def close(self) -> None:
        self.client.transport.close()
