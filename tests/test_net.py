"""Offline tests: The shared HTTP and feed helpers used by the open sources."""

import hashlib

import pytest
from helpers import EPUB, FakeClock, fixture, make_http

from bookfetch import feeds, net
from bookfetch.sources import arxiv, gutenberg, openlibrary


def test_pacer_waits_per_key_with_fake_clock():
    clock = FakeClock()
    pacer = net.Pacer(clock=clock, sleep=clock.sleep)
    pacer.wait("a", 3)
    clock.now += 1
    pacer.wait("a", 3)
    pacer.wait("b", 3)
    clock.now += 5
    pacer.wait("a", 3)
    assert clock.slept == [2.0]


def test_check_allows_only_listed_paths_and_upgrades_http():
    http = gutenberg.Source().http
    assert http.check("http://www.gutenberg.org/ebooks/1342.opds") == "https://www.gutenberg.org/ebooks/1342.opds"
    for url in (
        "https://www.gutenberg.org/ebooks/search/?query=x",
        "https://evil.example/ebooks/1.opds",
        "ftp://www.gutenberg.org/ebooks/1.opds",
    ):
        with pytest.raises(net.HttpError, match="refusing"):
            http.check(url)


def test_download_refuses_html_and_leaves_no_file(tmp_path):
    http, _, _ = make_http(
        gutenberg,
        {"https://www.gutenberg.org/ebooks/1.epub3.images": (200, b"<!DOCTYPE html><p>hi", {"Content-Type": "application/epub+zip"})},
    )
    with pytest.raises(net.HttpError, match="web page"):
        http.download("https://www.gutenberg.org/ebooks/1.epub3.images", tmp_path, name="x.epub", magic=b"PK")
    assert list(tmp_path.iterdir()) == []


def test_download_refuses_html_content_type(tmp_path):
    http, _, _ = make_http(
        gutenberg, {"https://www.gutenberg.org/ebooks/1.epub3.images": (200, EPUB, {"Content-Type": "text/html; charset=utf-8"})}
    )
    with pytest.raises(net.HttpError, match="web page"):
        http.download("https://www.gutenberg.org/ebooks/1.epub3.images", tmp_path, name="x.epub")
    assert list(tmp_path.iterdir()) == []


def test_download_checks_md5_and_size(tmp_path):
    url = "https://www.gutenberg.org/ebooks/1.epub3.images"
    http, _, _ = make_http(gutenberg, {url: (200, EPUB, {"Content-Type": "application/epub+zip"})})
    with pytest.raises(net.HttpError, match="checksum"):
        http.download(url, tmp_path, name="x.epub", md5="0" * 32)
    with pytest.raises(net.HttpError, match="incomplete"):
        http.download(url, tmp_path, name="x.epub", size=len(EPUB) + 1)
    assert list(tmp_path.iterdir()) == []
    path = http.download(url, tmp_path, name="x.epub", md5=hashlib.md5(EPUB).hexdigest(), size=len(EPUB), magic=b"PK")
    assert path.read_bytes() == EPUB and [p.name for p in tmp_path.iterdir()] == ["x.epub"]


def test_rate_limit_message():
    http, _, _ = make_http(arxiv, {"https://export.arxiv.org/api/query?x=1": (429, b"", {"Retry-After": "20"})})
    with pytest.raises(net.RateLimitedError, match="try again in 20 s"):
        http.get("https://export.arxiv.org/api/query?x=1")


def test_redirect_to_unexpected_host_is_refused(tmp_path):
    url = "https://archive.org/download/someitem00/someitem00.epub"
    http, web, _ = make_http(openlibrary, {url: (302, "https://evil.example/items/someitem00/someitem00.epub")})
    with pytest.raises(net.HttpError, match="refusing"):
        http.download(url, tmp_path)
    assert [r.full_url for r in web.requests] == [url]


def test_open_sources_send_the_bookfetch_user_agent():
    http, web, _ = make_http(arxiv, {"https://export.arxiv.org/api/query?q": (200, fixture("arxiv_empty.atom.xml"))})
    http.get("https://export.arxiv.org/api/query?q")
    assert web.requests[0].get_header("User-agent").startswith("bookfetch/")
    assert "github.com/soffits/bookfetch-cli" in web.requests[0].get_header("User-agent")


def test_feed_parser_rejects_html():
    with pytest.raises(feeds.FeedError):
        feeds.parse(fixture("se_interstitial.html"))


def test_human_size():
    assert net.human_size("831959") == "812.5 KB"
    assert net.human_size(None) == ""
    assert net.human_size(10) == "10 B"
