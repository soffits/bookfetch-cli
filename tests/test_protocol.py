"""``bookfetch source``: the media hub source protocol, with fake sources (no network)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bookfetch import cli, protocol
from bookfetch.common import BookfetchError, result

# Every source stays registered, the open ones included; nothing here reaches the network.
pytestmark = pytest.mark.open_sources


class FakeSource:
    def __init__(self, name, rows=None, error=None, files=None):
        self.name, self.rows, self.error, self.files = name, rows or [], error, files or {}
        self.searches, self.downloads = [], []

    def search(self, query, ext=None, lang=None, limit=10):
        self.searches.append((query, ext, lang, limit))
        if self.error:
            raise BookfetchError(self.error)
        return self.rows[:limit]

    def download(self, id, out=".", name=None):
        self.downloads.append(id)
        file_name, body = self.files[id]
        path = Path(out) / file_name
        path.write_bytes(body)
        return path


@pytest.fixture
def sources(monkeypatch):
    made = {}

    def fake_open(cfg, name, account):
        if name not in made:
            raise BookfetchError(f"{name}: not configured")
        return made[name]

    monkeypatch.setattr(protocol, "open_source", fake_open)
    return made


def run(capsys, *argv):
    code = cli.main(["source", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_search_answers_every_source_interleaved_in_the_protocol_shape(sources, capsys):
    sources["gutenberg"] = FakeSource(
        "gutenberg", [result("gutenberg", "1342", title="Pride and Prejudice", author="Jane Austen", language="en", extension="epub")]
    )
    sources["annas"] = FakeSource(
        "annas",
        [
            result(
                "annas",
                "a" * 32,
                title="Pride and Prejudice",
                author="Austen",
                language="English [en]",
                extension="pdf",
                size="7.5MB",
                year="2003",
            ),
            result("annas", "b" * 32, title="Pride (zh)", language="Chinese [zh]", extension="epub", size="300KB", year="1990"),
        ],
    )
    sources["openlibrary"] = FakeSource("openlibrary", error="rate limited")
    code, out, err = run(
        capsys,
        "--providers",
        "annas,gutenberg,openlibrary",
        "search",
        "--kind",
        "ebook",
        "--limit",
        "5",
        "--language",
        "en",
        "--json",
        "--",
        "pride",
    )
    assert code == 0
    results = json.loads(out)["results"]
    assert [r["id"] for r in results] == [f"annas:{'a' * 32}", "gutenberg:1342", f"annas:{'b' * 32}"]
    assert results[0] == {
        "id": f"annas:{'a' * 32}",
        "kind": "ebook",
        "title": "Pride and Prejudice",
        "author": "Austen",
        "publisher": "",
        "origin": "Anna's Archive",
        "language": "en",
        "year": 2003,
        "formats": [{"format": "pdf", "bytes": int(7.5 * 1024**2)}],
        "bytes": int(7.5 * 1024**2),
    }
    gutenberg = results[1]
    assert [f["format"] for f in gutenberg["formats"]] == ["epub", "azw3", "mobi", "txt"]
    assert gutenberg["cover"] == "https://www.gutenberg.org/cache/epub/1342/pg1342.cover.medium.jpg"
    assert sources["annas"].searches == [("pride", None, "en", 5)]
    assert "openlibrary: rate limited" in err


def test_search_filters_years_and_fails_only_when_every_source_failed(sources, capsys):
    sources["annas"] = FakeSource(
        "annas",
        [
            result("annas", "a" * 32, title="Old", year="1850", extension="epub"),
            result("annas", "b" * 32, title="New", year="2001", extension="epub"),
            result("annas", "c" * 32, title="Undated", extension="epub"),
        ],
    )
    code, out, _ = run(capsys, "--providers", "annas", "search", "--kind", "ebook", "--year-from", "1900", "--json", "--", "x")
    assert code == 0
    assert [r["title"] for r in json.loads(out)["results"]] == ["New", "Undated"]
    sources["annas"] = FakeSource("annas", error="login rejected")
    code, out, err = run(capsys, "--providers", "annas", "search", "--kind", "ebook", "--json", "--", "x")
    assert (code, out) == (1, "")
    assert err.strip().splitlines()[-1] == "bookfetch: annas: login rejected"


def test_papers_come_from_arxiv_and_zlib_is_left_out_by_default(sources, capsys):
    sources["arxiv"] = FakeSource("arxiv", [result("arxiv", "1706.03762", title="Attention Is All You Need", extension="pdf", year="2017")])
    code, out, _ = run(capsys, "search", "--kind", "paper", "--json", "--", "attention")
    assert code == 0
    assert json.loads(out)["results"][0]["kind"] == "paper"
    assert "zlib" not in protocol.DEFAULT_PROVIDERS


def test_download_writes_one_file_and_reports_its_size_and_hash(sources, capsys, tmp_path):
    body = b"Pride and Prejudice\n"
    sources["gutenberg"] = FakeSource("gutenberg", files={"1342:txt": ("Pride and Prejudice - Jane Austen.txt", body)})
    code, out, _ = run(capsys, "download", "--id=gutenberg:1342", "--format", "txt", "--output", str(tmp_path), "--json")
    assert code == 0
    done, final = (json.loads(line) for line in out.strip().splitlines())
    assert done == {
        "event": "done",
        "name": "Pride and Prejudice - Jane Austen.txt",
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    assert final["event"] == "result" and final["files"] == [{k: v for k, v in done.items() if k != "event"}]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Pride and Prejudice - Jane Austen.txt"], "the hidden download folder is gone"


def test_download_refuses_a_format_the_work_does_not_come_in(sources, capsys, tmp_path):
    sources["annas"] = FakeSource("annas", files={"a" * 32: ("book.pdf", b"%PDF-1.4")})
    code, out, err = run(capsys, "download", f"--id=annas:{'a' * 32}", "--format", "epub", "--output", str(tmp_path), "--json")
    assert (code, out) == (1, "")
    assert "the file is pdf, not epub" in err
    assert list(tmp_path.iterdir()) == []
    code, _, err = run(capsys, "download", "--id=gutenberg:1342", "--format", "pdf", "--output", str(tmp_path), "--json")
    assert code == 1 and "gutenberg has no pdf format" in err


def test_helpers():
    assert protocol.language_code("English [en]") == "en"
    assert protocol.language_code("eng") == "en"
    assert protocol.language_code("zh-Hans") == "zh-Hans"
    assert protocol.language_code("unknown language") == ""
    assert protocol.size_bytes("1.5 KB") == 1536
    assert protocol.size_bytes("") is None
    assert protocol.base_id("gutenberg", "1342:txt") == "1342"
    assert protocol.base_id("standardebooks", "jane-austen/emma:azw3") == "jane-austen/emma"
    assert protocol.base_id("annas", "abc") == "abc"


def test_a_cover_address_no_source_knows_is_refused(capsys, tmp_path):
    code, _, err = run(capsys, "cover", "--output", str(tmp_path / "c.jpg"), "--", "https://evil.example/c.jpg")
    assert code == 1 and "not a cover address" in err
