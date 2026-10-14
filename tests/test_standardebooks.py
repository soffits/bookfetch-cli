"""Offline tests: Standard Ebooks."""

import base64
import json
import urllib.parse
import urllib.request

import pytest
from helpers import EPUB, FakeClock, fixture, make_http

from bookfetch import net
from bookfetch.sources import standardebooks


def test_standardebooks_parse_search():
    rows = standardebooks.parse_search(fixture("se_search.opds.xml"))
    assert [r["id"] for r in rows] == ["jane-austen/pride-and-prejudice", "leo-tolstoy/the-death-of-ivan-ilyich/louise-maude_aylmer-maude"]
    assert rows[0]["size"] == "812.5 KB" and rows[0]["year"] == "2014" and rows[0]["language"] == "en-GB"
    azw = standardebooks.parse_search(fixture("se_search.opds.xml"), "azw3")
    assert [r["id"] for r in azw] == ["jane-austen/pride-and-prejudice:azw3"]


def test_standardebooks_ids_and_urls():
    assert standardebooks.parse_id("jane-austen/emma:kindle") == ("jane-austen/emma", "azw3")
    assert standardebooks.parse_id("https://standardebooks.org/ebooks/jane-austen/emma") == ("jane-austen/emma", "epub")
    assert (
        standardebooks.download_url("jane-austen/emma", "advanced")
        == "https://standardebooks.org/ebooks/jane-austen/emma/downloads/jane-austen_emma_advanced.epub?source=download"
    )
    for bad in ("honeypot", "../honeypot", "jane-austen/emma/../../honeypot", "Jane/Emma"):
        with pytest.raises(standardebooks.StandardEbooksError):
            standardebooks.parse_id(bad)


def test_standardebooks_never_requests_honeypot_or_feed_links():
    http = standardebooks.Source().http
    for url in (
        "https://standardebooks.org/honeypot",
        "https://standardebooks.org/feeds/opds",
        "https://standardebooks.org/ebooks/jane-austen/emma",
        "https://standardebooks.org/ebooks/jane-austen/emma/text/single-page",
    ):
        with pytest.raises(net.HttpError, match="refusing"):
            http.check(url)


def test_standardebooks_search_and_download_only_touch_fixed_endpoints(tmp_path):
    book = "jane-austen/pride-and-prejudice"
    file_url = standardebooks.download_url(book, "epub")
    routes = {
        "https://standardebooks.org/feeds/opds/all?*": (200, fixture("se_search.opds.xml"), {"Content-Type": "application/atom+xml"}),
        file_url: (
            200,
            EPUB,
            {"Content-Type": "application/epub+zip", "Content-Disposition": 'attachment; filename="jane-austen_pride-and-prejudice.epub"'},
        ),
    }
    http, web, _ = make_http(standardebooks, routes)
    source = standardebooks.Source(http=http, log=standardebooks.DownloadLog(tmp_path / "log.json"))
    assert source.search("austen", limit=5)
    path = source.download(book, out=tmp_path / "out")
    assert path.name == "jane-austen_pride-and-prejudice.epub"
    paths = [urllib.parse.urlsplit(r.full_url).path for r in web.requests]
    assert paths == ["/feeds/opds/all", "/ebooks/jane-austen/pride-and-prejudice/downloads/jane-austen_pride-and-prejudice.epub"]
    assert all("honeypot" not in r.full_url for r in web.requests)
    assert web.requests[1].full_url.endswith("?source=download")
    assert web.requests[0].get_header("Authorization") is None


def test_standardebooks_interstitial_page_is_refused(tmp_path):
    book = "jane-austen/pride-and-prejudice"
    routes = {
        standardebooks.download_url(book, "epub"): (200, fixture("se_interstitial.html"), {"Content-Type": "text/html; charset=utf-8"})
    }
    http, _, _ = make_http(standardebooks, routes)
    with pytest.raises(net.HttpError, match="web page"):
        standardebooks.Source(http=http).download(book, out=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_standardebooks_patron_basic_auth_header():
    secret = "patron@example.com"
    source = standardebooks.Source(email=secret)
    expected = "Basic " + base64.b64encode(b"patron@example.com:").decode()
    assert source.http.headers["Authorization"] == expected
    assert secret not in repr(source.account) and source.quota()["account"] == "Patrons Circle"
    assert "no download limit" in source.downloads_note


def test_standardebooks_auth_not_sent_after_redirect_to_other_host():
    source = standardebooks.Source(email="patron@example.com")
    handler = net._GuardedRedirects(source.http)
    source.http.rules.append(net.Rule(r"cdn\.standardebooks\.org", r"/.*"))
    req = urllib.request.Request("https://standardebooks.org/feeds/opds/all?query=x", headers=source.http.headers)
    new = handler.redirect_request(req, None, 302, "Found", {}, "https://cdn.standardebooks.org/file.epub")
    assert new.get_header("Authorization") is None


def test_standardebooks_download_log_paces_and_caps():
    clock = FakeClock(now=100000.0)
    log = standardebooks.DownloadLog(None, clock=clock, sleep=clock.sleep)
    times = []
    log._load = lambda: list(times)
    log._save = lambda t: times.__init__(t)
    for _ in range(30):
        log.reserve()
    assert clock.slept == []
    log.reserve()
    assert clock.slept == [30.0]
    times[:] = [clock.now - 60 - i for i in range(90)]
    with pytest.raises(standardebooks.StandardEbooksError, match="limit reached"):
        log.reserve()


def test_standardebooks_download_log_file(tmp_path):
    path = tmp_path / "state" / "se.json"
    log = standardebooks.DownloadLog(path)
    log.reserve()
    assert len(json.loads(path.read_text())) == 1


def test_standardebooks_patron_skips_log(tmp_path):
    book = "jane-austen/emma"
    routes = {standardebooks.download_url(book, "epub"): (200, EPUB, {"Content-Type": "application/epub+zip"})}
    http, web, _ = make_http(standardebooks, routes, headers={"Authorization": "Basic x"})
    log = standardebooks.DownloadLog(tmp_path / "log.json")
    standardebooks.Source(http=http, email="p@example.com", log=log).download(book, out=tmp_path)
    assert not (tmp_path / "log.json").exists()
