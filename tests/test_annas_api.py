import io
import json
import urllib.error
import urllib.parse
from http.cookiejar import Cookie
from pathlib import Path

import pytest

from bookfetch import cli
from bookfetch.common import USER_AGENT
from bookfetch.sources import annas
from bookfetch.sources.annas import AnnasError, Session

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


def use_session(monkeypatch, s):
    """Make the CLI use ``s`` for Anna's Archive and leave Z-Library unconfigured."""
    monkeypatch.setattr(annas.Source, "from_env", classmethod(lambda cls, env=None, key_file=None: cls(s)))
    for name in ("ZLIB_EMAIL", "ZLIB_PASSWORD", "ZLIB_PASSWORD_FILE", "BOOKFETCH_CDP_URL"):
        monkeypatch.delenv(name, raising=False)


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
    use_session(monkeypatch, s)
    assert cli.main(["download", "annas:" + "b" * 32, "--out", str(tmp_path), "--name", "my book.epub"]) == 0
    out = capsys.readouterr().out
    assert out == f"{tmp_path / 'my book.epub'} (4 bytes); annas downloads left today: 7\n"


def test_cli_search_text_and_json(monkeypatch, capsys):
    page = (FIXTURES / "search.html").read_bytes()
    s, _ = session({"/search?": page})
    use_session(monkeypatch, s)
    assert cli.main(["search", "pride", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert out == (
        "1. Pride and Prejudice\n   Austen, Jane | Example Press, 1998\n"
        "   English [en] · EPUB · 0.3MB · 1998\n   id annas:0123456789abcdef0123456789abcdef\n"
    )
    assert cli.main(["search", "pride", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 3
    assert rows[0]["source"] == "annas"
    assert rows[0]["id"] == "0123456789abcdef0123456789abcdef"
    assert rows[0]["extension"] == "epub"
    assert rows[0]["extra"]["info"].startswith("English [en]")


def test_cli_quota(monkeypatch, capsys):
    s, _ = session({"/account/": (FIXTURES / "account.html").read_bytes()})
    use_session(monkeypatch, s)
    assert cli.main(["quota"]) == 0
    assert capsys.readouterr().out == "annas: membership: Example Tier, fast downloads used: 3 / 50\n"


def test_cli_missing_key_exit_code(monkeypatch, capsys):
    for name in ("ANNAS_KEY", "ANNAS_KEY_FILE", "ZLIB_EMAIL", "ZLIB_PASSWORD", "ZLIB_PASSWORD_FILE", "BOOKFETCH_CDP_URL"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["quota"]) == 2
    err = capsys.readouterr().err
    assert "annas: skipped, no key" in err
    assert "zlib: skipped" in err
    assert cli.main(["download", "annas:" + "a" * 32]) == 2


def test_cli_api_error_exit_code(monkeypatch, capsys):
    s, _ = session({"fast_download.json": b'{"error": "Not a member"}'})
    use_session(monkeypatch, s)
    assert cli.main(["download", "annas:" + "c" * 32]) == 1
    err = capsys.readouterr().err
    assert "Not a member" in err
    assert "secret" not in err


def test_source_rejects_bad_md5(tmp_path):
    s, opener = session({})
    with pytest.raises(AnnasError, match="not an MD5"):
        annas.Source(s).download("nothex", out=tmp_path)
    assert opener.requests == []


def test_annas_key_file_flag(tmp_path, monkeypatch, capsys):
    f = tmp_path / "key"
    f.write_text("flagkey")
    seen = {}

    def fake_session(key, domain_list=None):
        seen["key"] = key
        s, _ = session({"/account/": (FIXTURES / "account.html").read_bytes()})
        return s

    monkeypatch.setattr(annas, "Session", fake_session)
    monkeypatch.delenv("ANNAS_KEY", raising=False)
    assert cli.main(["--annas-key-file", str(f), "quota", "--source", "annas"]) == 0
    assert seen["key"] == "flagkey"
