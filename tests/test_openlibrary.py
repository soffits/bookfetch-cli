"""Offline tests: Open Library and the Internet Archive."""

import hashlib
import json
import urllib.parse

import pytest
from helpers import EPUB, fixture, make_http

from bookfetch.sources import openlibrary


def test_openlibrary_search_filters_audio_lending_and_restricted():
    routes = {
        "https://openlibrary.org/search.json*": (200, fixture("ol_search.json"), {"Content-Type": "application/json"}),
        "https://archive.org/advancedsearch.php*": (200, fixture("ia_items.json"), {"Content-Type": "application/json"}),
    }
    http, web, clock = make_http(openlibrary, routes)
    rows = openlibrary.Source(http=http).search("pride and prejudice", lang="en", limit=5)
    assert [(r["id"], r["extension"]) for r in rows] == [("prideprejudice0000exam", "epub"), ("emmaexample00aust", "pdf")]
    assert rows[0]["publisher"] == "London : T. Nelson" and rows[0]["year"] == "1900" and rows[1]["year"] == "1896"
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(web.requests[0].full_url).query)
    assert q["ebook_access"] == ["public"] and q["q"] == ["pride and prejudice language:eng"]
    # The borrowable work is never asked about.
    assert "prideprejudicemo0000exam" not in web.requests[1].full_url
    # openlibrary.org and archive.org are paced separately (different services).
    assert len(web.requests) == 2 and clock.slept == []


def test_openlibrary_search_with_ext():
    routes = {
        "https://openlibrary.org/search.json*": (200, fixture("ol_search.json")),
        "https://archive.org/advancedsearch.php*": (200, fixture("ia_items.json")),
    }
    http, _, _ = make_http(openlibrary, routes)
    rows = openlibrary.Source(http=http).search("pride", ext="txt")
    assert [r["id"] for r in rows] == ["prideprejudice0000exam:txt", "emmaexample00aust:txt"]
    assert openlibrary.Source(http=http).search("pride", ext="mobi") == []


def test_openlibrary_choose_file_prefers_epub_and_skips_drm():
    meta = json.loads(fixture("ia_metadata_public.json"))
    assert openlibrary.choose_file(meta, None)[0]["name"] == "prideprejudice0000exam.epub"
    assert openlibrary.choose_file(meta, "pdf")[0]["name"] == "prideprejudice0000exam.pdf"
    assert openlibrary.choose_file(meta, "txt")[0]["name"] == "prideprejudice0000exam_djvu.txt"


def test_openlibrary_refuses_restricted_and_drm_even_if_not_flagged():
    meta = json.loads(fixture("ia_metadata_restricted.json"))
    with pytest.raises(openlibrary.OpenLibraryError, match="lending-only"):
        openlibrary.choose_file(meta, None)
    # Even without the item flag, private and encrypted files are never chosen.
    del meta["metadata"]["access-restricted-item"]
    with pytest.raises(openlibrary.OpenLibraryError, match="no public"):
        openlibrary.choose_file(meta, None)


def test_openlibrary_refuses_audio():
    with pytest.raises(openlibrary.OpenLibraryError, match="not a book"):
        openlibrary.choose_file(json.loads(fixture("ia_metadata_audio.json")), None)


def test_openlibrary_download_checks_md5(tmp_path):
    meta = json.loads(fixture("ia_metadata_public.json"))
    epub = next(f for f in meta["files"] if f["format"] == "EPUB")
    epub.update(md5=hashlib.md5(EPUB).hexdigest(), size=str(len(EPUB)))
    ident = "prideprejudice0000exam"
    routes = {
        f"https://archive.org/metadata/{ident}": (200, json.dumps(meta).encode()),
        f"https://archive.org/download/{ident}/{ident}.epub": (302, f"https://ia800100.us.archive.org/12/items/{ident}/{ident}.epub"),
        f"https://ia800100.us.archive.org/12/items/{ident}/{ident}.epub": (200, EPUB, {"Content-Type": "application/epub+zip"}),
    }
    http, web, clock = make_http(openlibrary, routes)
    path = openlibrary.Source(http=http).download(ident, out=tmp_path)
    assert path.name == f"{ident}.epub" and path.read_bytes() == EPUB
    assert len(web.requests) == 3


def test_openlibrary_lending_403(tmp_path):
    meta = json.loads(fixture("ia_metadata_public.json"))
    ident = "prideprejudice0000exam"
    routes = {
        f"https://archive.org/metadata/{ident}": (200, json.dumps(meta).encode()),
        f"https://archive.org/download/{ident}/{ident}.pdf": (403, b"", {"Content-Type": "text/html"}),
    }
    http, _, _ = make_http(openlibrary, routes)
    with pytest.raises(openlibrary.OpenLibraryError, match="lending-only"):
        openlibrary.Source(http=http).download(f"{ident}:pdf", out=tmp_path)


def test_openlibrary_ids():
    assert openlibrary.parse_id("prideprejudice0000exam:pdf") == ("prideprejudice0000exam", "pdf")
    with pytest.raises(openlibrary.OpenLibraryError):
        openlibrary.parse_id("../metadata")
