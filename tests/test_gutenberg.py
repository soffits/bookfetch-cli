"""Offline tests: Project Gutenberg."""

import pytest
from helpers import EPUB, fixture, make_http

from bookfetch.sources import gutenberg


def test_gutenberg_parse_search_skips_navigation_and_download_counts():
    rows = gutenberg.parse_search(fixture("gutenberg_search.opds.xml"))
    assert [r["id"] for r in rows] == ["1342", "10471", "45186"]
    assert rows[0]["author"] == "Jane Austen" and rows[0]["extension"] == "epub"
    assert rows[1]["author"] == ""
    assert rows[2]["title"] == "Ylpeys ja ennakkoluulo" and rows[2]["language"] == "Finnish"


def test_gutenberg_ext_and_lang_mapping():
    assert [r["id"] for r in gutenberg.parse_search(fixture("gutenberg_search.opds.xml"), "txt")][0] == "1342:txt"
    assert gutenberg.parse_search(fixture("gutenberg_search.opds.xml"), "pdf") == []
    assert gutenberg.search_url("austen", "FR").endswith("query=austen+l.fr")
    assert gutenberg.parse_id("1342") == ("1342", "epub")
    assert gutenberg.parse_id("1342:kindle") == ("1342", "azw3")
    with pytest.raises(gutenberg.GutenbergError):
        gutenberg.parse_id("1342/../x")


def test_gutenberg_download_fetches_entry_then_file(tmp_path):
    routes = {
        "https://www.gutenberg.org/ebooks/1342.opds": (200, fixture("gutenberg_book.opds.xml"), {"Content-Type": "application/atom+xml"}),
        "https://www.gutenberg.org/ebooks/1342.epub3.images": (302, "http://www.gutenberg.org/cache/epub/1342/pg1342-images-3.epub"),
        "https://www.gutenberg.org/cache/epub/1342/pg1342-images-3.epub": (200, EPUB, {"Content-Type": "application/epub+zip"}),
    }
    http, web, clock = make_http(gutenberg, routes)
    path = gutenberg.Source(http=http).download("1342", out=tmp_path)
    assert path.name == "Pride and Prejudice - Austen, Jane.epub" and path.read_bytes() == EPUB
    # http:// in the redirect was upgraded to https, and the gap between requests kept.
    assert [r.full_url for r in web.requests][-1].startswith("https://www.gutenberg.org/cache/")
    # One gap before the file request; the redirect hop belongs to the same request.
    assert clock.slept == [2.0]


def test_gutenberg_missing_format(tmp_path):
    routes = {"https://www.gutenberg.org/ebooks/1342.opds": (200, fixture("gutenberg_book.opds.xml"))}
    http, _, _ = make_http(gutenberg, routes)
    source = gutenberg.Source(http=http)
    feed = fixture("gutenberg_book.opds.xml").replace(b"1342.kf8.images", b"1342.other")
    routes["https://www.gutenberg.org/ebooks/1342.opds"] = (200, feed)
    with pytest.raises(gutenberg.GutenbergError, match="no azw3 file"):
        source.download("1342:azw3", out=tmp_path)
