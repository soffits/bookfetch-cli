import io
import json
import urllib.error
import urllib.parse
from http.cookiejar import Cookie
from pathlib import Path

import pytest

from annas_archive_cli import cli
from annas_archive_cli.client import USER_AGENT, AnnasError, Session

FIXTURES = Path(__file__).parent / "fixtures"


def make_cookie(name):
    return Cookie(0, name, "x", None, False, "a.example", False, False, "/", True, True, None, False, None, None, {})


class FakeOpener:
    """Stands in for urllib's opener; maps URL prefixes to responses."""

    def __init__(self, session_holder, routes, login_ok=True):
        self.holder = session_holder
        self.routes = routes
        self.login_ok = login_ok
        self.requests = []

    def open(self, req, timeout=None):
        url = req.full_url
        self.requests.append(req)
        if url.endswith("/account/") and req.data is not None:
            if isinstance(self.login_ok, Exception):
                raise self.login_ok
            if self.login_ok:
                self.holder[0].jar.set_cookie(make_cookie("aa_account_id2"))
            return io.BytesIO(b"ok")
        for prefix, response in self.routes.items():
            if prefix in url:
                if isinstance(response, Exception):
                    raise response
                return io.BytesIO(response)
        raise AssertionError(f"unexpected URL {url}")


def session(routes, login_ok=True, domain_list=("a.example",)):
    holder = []
    opener = FakeOpener(holder, routes, login_ok)
    s = Session("secret", opener=opener, domain_list=list(domain_list))
    holder.append(s)
    return s, opener


def http_error(code, body):
    return urllib.error.HTTPError("https://a.example/x", code, "err", {}, io.BytesIO(body))


def test_login_posts_key_with_browser_user_agent():
    s, opener = session({})
    s.login()
    assert s.base == "https://a.example"
    req = opener.requests[0]
    assert req.get_header("User-agent") == USER_AGENT
    assert urllib.parse.parse_qs(req.data.decode()) == {"key": ["secret"]}


def test_login_rejected_tries_all_domains():
    s, opener = session({}, login_ok=False, domain_list=("a.example", "b.example"))
    with pytest.raises(AnnasError, match="a.example: key rejected; b.example: key rejected"):
        s.login()
    assert len(opener.requests) == 2


def test_login_network_error():
    s, _ = session({}, login_ok=urllib.error.URLError("down"))
    with pytest.raises(AnnasError, match="login failed: a.example: .*down"):
        s.login()


def test_search_builds_query_and_parses():
    page = (FIXTURES / "search.html").read_bytes()
    s, opener = session({"/search?": page})
    rows = s.search("pride prejudice", ext="epub", lang="en", limit=2)
    assert len(rows) == 2
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.requests[-1].full_url).query)
    assert qs == {"q": ["pride prejudice"], "ext": ["epub"], "lang": ["en"]}


def test_search_http_error_mentions_membership():
    s, _ = session({"/search?": http_error(403, b"blocked")})
    with pytest.raises(AnnasError, match="HTTP 403.*membership"):
        s.search("x")


def test_api_error_from_json_body():
    body = json.dumps({"error": "Invalid secret key"}).encode()
    s, _ = session({"fast_download.json": http_error(401, body)})
    with pytest.raises(AnnasError, match="API error: Invalid secret key"):
        s.fast_download_info("0" * 32)


def test_api_missing_download_url():
    s, _ = session({"fast_download.json": b"{}"})
    with pytest.raises(AnnasError, match="no download_url"):
        s.fast_download_info("0" * 32)


def test_api_not_json():
    s, _ = session({"fast_download.json": b"<html>"})
    with pytest.raises(AnnasError, match="not JSON"):
        s.fast_download_info("0" * 32)


def test_api_sends_md5_and_key():
    body = json.dumps({"download_url": "https://dl.example/f/book.epub"}).encode()
    s, opener = session({"fast_download.json": body})
    assert s.fast_download_info("a" * 32)["download_url"].endswith("book.epub")
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.requests[-1].full_url).query)
    assert qs == {"md5": ["a" * 32], "key": ["secret"]}


def test_download_writes_file(tmp_path):
    body = json.dumps(
        {"download_url": "https://dl.example/f/Pride%3A%20and%20Prejudice.epub?x=1", "account_fast_download_info": {"downloads_left": 49}}
    ).encode()
    s, _ = session({"fast_download.json": body, "dl.example/f/": b"PK-epub-bytes"})
    path, data = s.download("a" * 32, out=tmp_path / "out")
    assert path == tmp_path / "out" / "Pride_ and Prejudice.epub"
    assert path.read_bytes() == b"PK-epub-bytes"
    assert data["account_fast_download_info"]["downloads_left"] == 49


def test_cli_download_output(tmp_path, monkeypatch, capsys):
    body = json.dumps({"download_url": "https://dl.example/f/x.epub", "account_fast_download_info": {"downloads_left": 7}}).encode()
    s, _ = session({"fast_download.json": body, "dl.example/f/": b"data"})
    monkeypatch.setattr(cli, "Session", lambda key: s)
    monkeypatch.setenv("ANNAS_KEY", "secret")
    assert cli.main(["download", "b" * 32, "--out", str(tmp_path), "--name", "my book.epub"]) == 0
    out = capsys.readouterr().out
    assert out == f"{tmp_path / 'my book.epub'} (4 bytes); downloads left today: 7\n"


def test_cli_search_text_and_json(monkeypatch, capsys):
    page = (FIXTURES / "search.html").read_bytes()
    s, _ = session({"/search?": page})
    monkeypatch.setattr(cli, "Session", lambda key: s)
    monkeypatch.setenv("ANNAS_KEY", "secret")
    assert cli.main(["search", "pride", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("1. Pride and Prejudice\n   Austen, Jane | Example Press, 1998\n")
    assert "md5 0123456789abcdef0123456789abcdef" in out
    assert cli.main(["search", "pride", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_cli_quota(monkeypatch, capsys):
    s, _ = session({"/account/": (FIXTURES / "account.html").read_bytes()})
    monkeypatch.setattr(cli, "Session", lambda key: s)
    monkeypatch.setenv("ANNAS_KEY", "secret")
    assert cli.main(["quota"]) == 0
    assert capsys.readouterr().out == "membership: Example Tier\nfast downloads used: 3 / 50\n"


def test_cli_missing_key_exit_code(monkeypatch, capsys):
    monkeypatch.delenv("ANNAS_KEY", raising=False)
    monkeypatch.delenv("ANNAS_KEY_FILE", raising=False)
    assert cli.main(["quota"]) == 2
    assert "no key" in capsys.readouterr().err


def test_cli_api_error_exit_code(monkeypatch, capsys):
    s, _ = session({"fast_download.json": b'{"error": "Not a member"}'})
    monkeypatch.setattr(cli, "Session", lambda key: s)
    monkeypatch.setenv("ANNAS_KEY", "secret")
    assert cli.main(["download", "c" * 32]) == 1
    err = capsys.readouterr().err
    assert "Not a member" in err
    assert "secret" not in err
