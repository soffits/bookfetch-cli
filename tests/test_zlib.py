import io
import json
import urllib.parse
from pathlib import Path

import pytest

from bookfetch.common import MissingConfigError
from bookfetch.sources import zlib
from bookfetch.sources.zlib import BOT_WALL, Client, Credentials, ZlibError

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH = (FIXTURES / "zlib_search.json").read_text()
PROFILE = json.dumps({"success": 1, "user": {"downloads_today": 3, "downloads_limit": 10}})
LINK = "https://dl.example/books/Pride%20and%20Prejudice.epub?md5=x"
FILE = json.dumps({"success": 1, "file": {"downloadLink": LINK, "extension": "epub", "allowDownload": True}})


class FakeTransport:
    """Maps (method, path) to (status, text) and records calls."""

    def __init__(self, routes, browser=False, file_bytes=None):
        self.routes = routes
        self.browser = browser
        self.file_bytes = file_bytes
        self.calls = []
        self.closed = False

    def request(self, method, path, form=None):
        self.calls.append((method, path, form))
        response = self.routes.get((method, path))
        if response is None:
            raise AssertionError(f"unexpected {method} {path}")
        return response

    def fetch_file(self, url):
        self.calls.append(("FILE", url, None))
        if self.file_bytes is None:
            raise ZlibError("blocked")
        return self.file_bytes

    def close(self):
        self.closed = True


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


LOGIN = ("POST", "/eapi/user/login")
ROUTES = {
    LOGIN: (200, json.dumps({"success": 1, "user": {"id": 1}})),
    ("GET", "/eapi/user/profile"): (200, PROFILE),
    ("POST", "/eapi/book/search"): (200, SEARCH),
    ("GET", "/eapi/book/1000001/a1b2c3/file"): (200, FILE),
}


def source(routes=None, browser=False, opener=None, file_bytes=None, creds=True):
    t = FakeTransport(dict(ROUTES if routes is None else routes), browser=browser, file_bytes=file_bytes)
    c = Client(t, Credentials("reader@example.com", "pw") if creds else None, opener=opener)
    return zlib.Source(c), t


def test_credentials_from_env(tmp_path):
    f = tmp_path / "pw"
    f.write_text("filepw\n")
    c = zlib.load_credentials({"ZLIB_EMAIL": "a@example.com", "ZLIB_PASSWORD_FILE": str(f)})
    assert (c.email, c.password) == ("a@example.com", "filepw")
    assert "filepw" not in repr(c)
    c = zlib.load_credentials({"ZLIB_EMAIL": "a@example.com", "ZLIB_PASSWORD": "envpw", "ZLIB_PASSWORD_FILE": str(f)})
    assert c.password == "envpw"
    assert zlib.load_credentials({}) is None
    with pytest.raises(MissingConfigError, match="ZLIB_EMAIL"):
        zlib.load_credentials({"ZLIB_PASSWORD": "x"})
    with pytest.raises(MissingConfigError, match="no password"):
        zlib.load_credentials({"ZLIB_EMAIL": "a@example.com"})


def test_domains_and_from_env():
    assert zlib.domains({}) == ["z-lib.sk"]
    assert zlib.domains({"ZLIB_DOMAINS": "x.example, y.example"}) == ["x.example", "y.example"]
    with pytest.raises(MissingConfigError, match="not configured"):
        zlib.Source.from_env({})
    s = zlib.Source.from_env({"ZLIB_EMAIL": "a@example.com", "ZLIB_PASSWORD": "pw"})
    assert isinstance(s.client.transport, zlib.HttpTransport)
    assert s.client.transport.base == "https://z-lib.sk"
    s = zlib.Source.from_env({"BOOKFETCH_CDP_URL": "http://127.0.0.1:9222", "ZLIB_DOMAINS": "x.example"})
    assert isinstance(s.client.transport, zlib.BrowserTransport)
    assert s.client.transport.domain == "x.example"


def test_parse_id():
    assert zlib.parse_id(" 123/abc9 ") == ("123", "abc9")
    for bad in ("123", "abc/def", "1/2/3", ""):
        with pytest.raises(ZlibError, match="ID/HASH"):
            zlib.parse_id(bad)


def test_search_form_and_results():
    s, t = source()
    rows = s.search("pride", ext="EPUB", lang="en", limit=5)
    login, search = t.calls
    assert login[0:2] == LOGIN
    assert dict(login[2]) == {"email": "reader@example.com", "password": "pw"}
    assert search[2] == [("message", "pride"), ("limit", "5"), ("extensions[]", "epub"), ("languages[]", "english")]
    assert rows[0] == {
        "source": "zlib",
        "id": "1000001/a1b2c3",
        "title": "Pride and Prejudice",
        "author": "Jane Austen",
        "publisher": "Example Press",
        "year": "1998",
        "language": "English",
        "extension": "epub",
        "size": "398 KB",
        "extra": {"md5": "0123456789abcdef0123456789abcdef", "filesize": 407595},
    }
    assert rows[1]["year"] == ""
    assert rows[1]["extra"] == {"series": "Example Classics", "pages": 600}
    assert len(s.search("pride", limit=1)) == 1


def test_unknown_language_passed_through():
    assert ("languages[]", "esperanto") in zlib.search_form("q", None, "Esperanto", 3)


def test_bot_wall_message():
    s, _ = source({**ROUTES, ("POST", "/eapi/book/search"): (513, "<title>Verifying your browser | DiamWall</title>")})
    with pytest.raises(ZlibError, match="BOOKFETCH_CDP_URL"):
        s.search("pride")
    assert "remote-debugging-port" in BOT_WALL


def test_login_failure_message():
    s, _ = source({LOGIN: (200, json.dumps({"success": 0, "error": "Incorrect email or password"}))})
    with pytest.raises(ZlibError, match="login: Incorrect email or password"):
        s.quota()


def test_not_json():
    s, _ = source({**ROUTES, ("GET", "/eapi/user/profile"): (502, "<html>bad gateway</html>")})
    with pytest.raises(ZlibError, match="HTTP 502, response was not JSON"):
        s.quota()


def test_browser_session_already_logged_in_skips_login():
    s, t = source(browser=True, creds=False)
    assert s.quota() == {"downloads_today": 3, "downloads_limit": 10}
    assert LOGIN not in [c[0:2] for c in t.calls]


def test_browser_logs_in_when_needed():
    routes = {**ROUTES, ("GET", "/eapi/user/profile"): (200, json.dumps({"success": 0, "error": "Please login"}))}
    s, t = source(routes, browser=True)
    with pytest.raises(ZlibError, match="profile: Please login"):
        s.quota()
    assert LOGIN in [c[0:2] for c in t.calls]


def test_not_logged_in_without_credentials():
    routes = {("GET", "/eapi/user/profile"): (200, json.dumps({"success": 0}))}
    s, _ = source(routes, browser=True, creds=False)
    with pytest.raises(MissingConfigError, match="ZLIB_EMAIL"):
        s.quota()


def test_download_plain_http(tmp_path):
    headers = {
        "Content-Type": "application/epub+zip",
        "Content-Disposition": 'attachment; filename="x.epub"; '
        "filename*=UTF-8''Pride%20and%20Prejudice%20(Jane%20Austen)%20(z-lib.sk%2C%201lib.sk).epub",
    }
    opener = FakeOpener(FakeResponse(b"PK\x03\x04epub", headers))
    s, t = source(opener=opener)
    path = s.download("1000001/a1b2c3", out=tmp_path / "out")
    assert path == tmp_path / "out" / "Pride and Prejudice (Jane Austen).epub"
    assert path.read_bytes() == b"PK\x03\x04epub"
    assert opener.requests[0].full_url == LINK
    assert s.downloads_left == 7
    assert ("FILE", LINK, None) not in t.calls


def test_download_falls_back_to_browser(tmp_path):
    opener = FakeOpener(FakeResponse(b"<html>check</html>", {"Content-Type": "text/html"}))
    s, t = source(browser=True, opener=opener, file_bytes=b"PKdata")
    path = s.download("1000001/a1b2c3", out=tmp_path, name="mine.epub")
    assert path == tmp_path / "mine.epub"
    assert path.read_bytes() == b"PKdata"
    assert ("FILE", LINK, None) in t.calls


def test_download_blocked_without_browser(tmp_path):
    import urllib.error

    opener = FakeOpener(urllib.error.HTTPError(LINK, 403, "no", {}, io.BytesIO(b"")))
    s, _ = source(opener=opener)
    with pytest.raises(ZlibError, match="blocked"):
        s.download("1000001/a1b2c3", out=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_no_link(tmp_path):
    routes = {**ROUTES, ("GET", "/eapi/book/1000001/a1b2c3/file"): (200, json.dumps({"success": 1, "file": {}}))}
    s, _ = source(routes)
    with pytest.raises(ZlibError, match="no download link"):
        s.download("1000001/a1b2c3", out=tmp_path)


def test_tidy_name():
    assert zlib.tidy_name("Book (Author) (z-library.sk, 1lib.sk, z-lib.sk).epub") == "Book (Author).epub"
    assert zlib.tidy_name("Plain Title.pdf") == "Plain Title.pdf"


class FakePage:
    def __init__(self, values):
        self.values = list(values)
        self.scripts = []

    def evaluate(self, expression):
        self.scripts.append(expression)
        return self.values.pop(0)


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.opened = []
        self.closed = []

    def new_tab(self, url):
        self.opened.append(url)
        return {"id": "T1", "webSocketDebuggerUrl": "ws://x"}

    def attach(self, target):
        return self.page

    def close_tab(self, target_id):
        self.closed.append(target_id)


def test_browser_transport_runs_fetch_in_own_tab():
    page = FakePage([
        {"host": "", "ready": "complete", "title": ""},
        {"host": "z.example", "ready": "interactive", "title": "Z"},
        {"status": 200, "text": SEARCH},
    ])  # fmt: skip
    page.ws = type("WS", (), {"close": lambda self: None})()
    browser = FakeBrowser(page)
    t = zlib.BrowserTransport("http://cdp", "z.example", browser=browser)
    status, text = t.request("POST", "/eapi/book/search", [("message", "a&b")])
    assert status == 200 and json.loads(text)["success"] == 1
    assert browser.opened == ["https://z.example/"]
    script = page.scripts[-1]
    args = json.loads(script[script.index("const a = ") + 10 : script.index(";\n")])
    assert args == {"method": "POST", "path": "/eapi/book/search", "body": urllib.parse.urlencode([("message", "a&b")])}
    t.close()
    assert browser.closed == ["T1"]


def test_browser_transport_fetch_file_decodes_base64():
    page = FakePage(
        [{"host": "z.example", "ready": "complete", "title": "Z"}, {"status": 200, "type": "application/epub+zip", "b64": "UEsDBA=="}]
    )
    t = zlib.BrowserTransport("http://cdp", "z.example", browser=FakeBrowser(page))
    assert t.fetch_file(LINK) == b"PK\x03\x04"
