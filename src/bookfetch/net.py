"""A small, polite HTTP helper for the open sources (Gutenberg, Open Library, arXiv, Standard Ebooks).

- Only URLs that match the source's allow list are fetched, including every
  redirect hop, so nothing ever follows an arbitrary link from a page.
- Requests to the same host (or host group) are spaced by a minimum interval.
- Downloads refuse HTML when a file is expected, check the size and the MD5
  when known, and are written to a temporary file that is renamed at the end.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from . import __version__
from .common import BookfetchError, filename_from_disposition, filename_from_url, safe_filename

OPEN_USER_AGENT = f"bookfetch/{__version__} (+https://github.com/soffits/bookfetch-cli)"
SEARCH_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 600
CHUNK = 1 << 20
HTML_START = (b"<!doctype html", b"<html")


class HttpError(BookfetchError):
    """An HTTP error from an open source; ``status`` is the HTTP status (0 for network errors)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class RateLimitedError(HttpError):
    """The site asked us to slow down (HTTP 429 or 503)."""


class Pacer:
    """Keeps at least ``interval`` seconds between calls to ``wait`` (per key)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.clock = clock
        self.sleep = sleep
        self.last: dict[str, float] = {}

    def wait(self, key: str, interval: float) -> None:
        if interval > 0 and key in self.last:
            left = self.last[key] + interval - self.clock()
            if left > 0:
                self.sleep(left)
        self.last[key] = self.clock()


class Rule:
    """Allows URLs whose host matches ``host`` (a regex) and whose path matches ``path`` (a regex)."""

    def __init__(self, host: str, path: str):
        self.host = re.compile(host)
        self.path = re.compile(path)

    def allows(self, host: str, path: str) -> bool:
        return bool(self.host.fullmatch(host) and self.path.fullmatch(path))


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, client: Http):
        self.client = client

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = self.client.check(urllib.parse.urljoin(req.full_url, newurl))
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


class Http:
    """HTTP for one source.

    ``rules``: where requests may go. ``intervals``: minimum seconds between
    requests per host; ``groups`` maps a host to the key whose interval it
    shares, either a dict (for example export.arxiv.org -> arxiv.org) or a
    function of the host (for the many archive.org data servers).
    """

    def __init__(
        self,
        name: str,
        rules: Iterable[Rule],
        intervals: dict[str, float] | None = None,
        groups: dict[str, str] | Callable[[str], str] | None = None,
        headers: dict[str, str] | None = None,
        opener=None,
        pacer: Pacer | None = None,
        upgrade_http: bool = True,
    ):
        self.name = name
        self.rules = list(rules)
        self.intervals = intervals or {}
        self.groups = groups or {}
        self.headers = {"User-Agent": OPEN_USER_AGENT, **(headers or {})}
        self.opener = opener or urllib.request.build_opener(_GuardedRedirects(self))
        self.pacer = pacer or Pacer()
        self.upgrade_http = upgrade_http
        self.requested: list[str] = []

    def check(self, url: str) -> str:
        """Return ``url`` (upgraded to https) if a rule allows it; raise otherwise."""
        u = urllib.parse.urlsplit(url)
        if u.scheme == "http" and self.upgrade_http:
            u = u._replace(scheme="https")
        host = (u.hostname or "").lower()
        if u.scheme != "https" or not any(r.allows(host, u.path) for r in self.rules):
            raise HttpError(f"{self.name}: refusing to fetch {u.scheme}://{host}{u.path} (not an expected address)")
        return urllib.parse.urlunsplit(u)

    def _wait(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).hostname or ""
        key = self.groups(host) if callable(self.groups) else self.groups.get(host, host)
        self.pacer.wait(key, self.intervals.get(key, 0))

    def open(self, url: str, accept: str | None = None, timeout: float = SEARCH_TIMEOUT, headers: dict[str, str] | None = None):
        url = self.check(url)
        self._wait(url)
        self.requested.append(url)
        req = urllib.request.Request(url, headers={**self.headers, **({"Accept": accept} if accept else {}), **(headers or {})})
        try:
            return self.opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise self._error(e) from None
        except (OSError, http.client.HTTPException) as e:
            raise HttpError(f"{self.name}: request failed: {getattr(e, 'reason', None) or e}") from None

    def _error(self, e: urllib.error.HTTPError) -> HttpError:
        if e.code in (429, 503):
            wait = e.headers.get("Retry-After") if e.headers else None
            hint = f"; try again in {wait} s" if wait and wait.strip().isdigit() else "; try again later"
            return RateLimitedError(f"{self.name}: rate limited (HTTP {e.code}){hint}", e.code)
        return HttpError(f"{self.name}: HTTP {e.code} for {urllib.parse.urlsplit(e.geturl() or '').path or 'request'}", e.code)

    def get(self, url: str, accept: str | None = None, timeout: float = SEARCH_TIMEOUT, limit: int = 20 << 20) -> bytes:
        with self.open(url, accept=accept, timeout=timeout) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise HttpError(f"{self.name}: response too large")
        return data

    def download(
        self,
        url: str,
        out: str | Path,
        name: str | None = None,
        fallback: str = "download",
        md5: str | None = None,
        size: int | None = None,
        magic: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Path:
        """Save ``url`` into directory ``out``; returns the final path.

        Refuses HTML (a page instead of the file), checks ``magic`` (expected
        leading bytes), Content-Length or ``size``, and ``md5`` when given. The
        file only appears under its final name once everything checks out.
        """
        out_dir = Path(out).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        with self.open(url, timeout=DOWNLOAD_TIMEOUT, headers=headers) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype in ("text/html", "application/xhtml+xml"):
                raise HttpError(f"{self.name}: got a web page instead of the file ({ctype}); nothing saved")
            served = filename_from_disposition(r.headers.get("Content-Disposition")) or filename_from_url(r.geturl() or url)
            path = out_dir / safe_filename(name or served or fallback, fallback=fallback)
            part = path.with_name(path.name + ".part")
            expected = _int(r.headers.get("Content-Length"))
            digest = hashlib.md5(usedforsecurity=False)
            written = 0
            try:
                with open(part, "wb") as f:
                    first = True
                    while chunk := r.read(CHUNK):
                        if first:
                            _check_start(self.name, chunk, magic)
                            first = False
                        f.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                if written == 0:
                    raise HttpError(f"{self.name}: empty response; nothing saved")
                for want in (expected, size):
                    if want is not None and written != want:
                        raise HttpError(f"{self.name}: incomplete download ({written} of {want} bytes); nothing saved")
                if md5 and digest.hexdigest() != md5.lower():
                    raise HttpError(f"{self.name}: checksum mismatch; nothing saved")
                part.replace(path)
            except (OSError, http.client.HTTPException) as e:
                part.unlink(missing_ok=True)
                raise HttpError(f"{self.name}: download failed: {e}") from None
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        return path


def _check_start(name: str, chunk: bytes, magic: bytes | None) -> None:
    head = chunk[:200].lstrip().lower()
    if head.startswith(HTML_START):
        raise HttpError(f"{name}: got a web page instead of the file; nothing saved")
    if magic and not chunk.startswith(magic):
        raise HttpError(f"{name}: the file does not look like the expected type; nothing saved")


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def human_size(n) -> str:
    """Bytes as a short human-readable size ('' when unknown)."""
    size = _int(n)
    if size is None or size < 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""
