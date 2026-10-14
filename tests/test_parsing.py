from pathlib import Path

from bookfetch.common import filename_from_url, safe_filename
from bookfetch.sources.annas import parse_quota, parse_results

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_results_fields():
    rows = parse_results((FIXTURES / "search.html").read_text(encoding="utf-8"))
    assert [r["md5"] for r in rows] == [
        "0123456789abcdef0123456789abcdef",
        "fedcba9876543210fedcba9876543210",
        "00000000000000000000000000000abc",
    ]
    first = rows[0]
    assert first["title"] == "Pride and Prejudice"
    assert first["author"] == "Austen, Jane"
    assert first["publisher"] == "Example Press, 1998"
    assert first["info"] == "English [en] · EPUB · 0.3MB · 1998 · 📘 Book (fiction)"


def test_parse_results_missing_fields_and_entities():
    rows = parse_results((FIXTURES / "search.html").read_text(encoding="utf-8"))
    assert rows[1]["publisher"] == ""
    assert rows[1]["info"] == "English [en] · PDF · 2.1MB · 1851"
    assert rows[2]["title"] == "Les Misérables & Other Tales"
    assert rows[2]["author"] == ""


def test_parse_results_empty_page():
    assert parse_results("<html><body>No files found.</body></html>") == []


def test_parse_quota():
    q = parse_quota((FIXTURES / "account.html").read_text(encoding="utf-8"))
    assert q == {"membership": "Example Tier", "fast_downloads_used": "3 / 50"}


def test_parse_quota_unknown():
    assert parse_quota("<html></html>") == {"membership": "?", "fast_downloads_used": "?"}


def test_safe_filename():
    assert safe_filename('a/b\\c:d*e?f"g<h>i|j.epub') == "a_b_c_d_e_f_g_h_i_j.epub"
    assert safe_filename("tab\there") == "tab_here"
    assert safe_filename("x" * 300) == "x" * 200
    assert safe_filename("..", fallback="abc") == "abc"
    assert safe_filename("", fallback="abc") == "abc"


def test_filename_from_url():
    assert filename_from_url("https://dl.example/d/Pride%20and%20Prejudice.epub?token=1") == "Pride and Prejudice.epub"
    assert filename_from_url("https://dl.example/") == ""


def test_safe_filename_keeps_extension_when_long():
    name = safe_filename("y" * 300 + ".epub")
    assert len(name) == 200
    assert name.endswith(".epub")


def test_split_info():
    from bookfetch.sources.annas import split_info

    assert split_info("English [en] · EPUB · 0.3MB · 1998 · 📘 Book (fiction)") == {
        "language": "English [en]",
        "extension": "epub",
        "size": "0.3MB",
        "year": "1998",
    }
    assert split_info("") == {"language": "", "extension": "", "size": "", "year": ""}
