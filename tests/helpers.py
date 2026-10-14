"""Shared pieces for the open-source tests: a fake web behind urllib's real redirect handling, and a fake clock."""

import email.message
import io
import urllib.request
import urllib.response
from pathlib import Path

from bookfetch import net

FIXTURES = Path(__file__).parent / "fixtures"
EPUB = b"PK\x03\x04" + b"mimetypeapplication/epub+zip" + b"\x00" * 64
PDF = b"%PDF-1.5\n" + b"\x00" * 64


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeWeb(urllib.request.HTTPSHandler):
    """Answers https requests from a table; everything goes through urllib's real redirect handling.

    routes: {url or url prefix ending in '*': (status, body, headers) or (302, location)}.
    """

    def __init__(self, routes):
        super().__init__()
        self.routes = routes
        self.requests = []

    def _match(self, url):
        if url in self.routes:
            return self.routes[url]
        for key, value in self.routes.items():
            if key.endswith("*") and url.startswith(key[:-1]):
                return value
        return (404, b"not found", {"Content-Type": "text/plain"})

    def https_open(self, req):
        self.requests.append(req)
        route = self._match(req.full_url)
        headers = email.message.Message()
        if route[0] in (301, 302):
            headers["Location"] = route[1]
            body = b""
        else:
            body = route[1]
            for k, v in (route[2] if len(route) > 2 else {}).items():
                headers[k] = v
            if "Content-Length" not in headers:
                headers["Content-Length"] = str(len(body))
        resp = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, route[0])
        resp.msg = "OK" if route[0] == 200 else "Error"
        return resp


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def make_http(module, routes, clock=None, headers=None):
    """An Http for ``module`` whose opener is the fake web plus the real guarded redirect handler."""
    clock = clock or FakeClock()
    source = module.Source.__new__(module.Source)
    source.email = ""
    http = module.Source.make_http(source)
    web = FakeWeb(routes)
    http.opener = urllib.request.build_opener(net._GuardedRedirects(http), web)
    http.pacer = net.Pacer(clock=clock, sleep=clock.sleep)
    if headers:
        http.headers.update(headers)
    return http, web, clock
