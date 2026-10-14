import json

import pytest

from bookfetch import cli
from bookfetch.common import result
from bookfetch.sources import annas, zlib
from bookfetch.sources.zlib import ZlibError


class StubSource:
    def __init__(self, name, rows=(), error=None):
        self.name = name
        self.rows = list(rows)
        self.error = error
        self.calls = []
        self.closed = False
        self.downloads_left = 9

    def search(self, query, ext=None, lang=None, limit=10):
        self.calls.append(("search", query, ext, lang, limit))
        if self.error:
            raise self.error
        return self.rows[:limit]

    def download(self, id, out=".", name=None):
        self.calls.append(("download", id, out, name))
        from pathlib import Path

        p = Path(out) / (name or "book.epub")
        p.write_bytes(b"PK")
        return p

    def quota(self):
        if self.error:
            raise self.error
        return {"downloads_today": 1, "downloads_limit": 10}

    def close(self):
        self.closed = True


def install(monkeypatch, **stubs):
    """stubs: source name -> StubSource or an exception raised by from_account.

    Each configured source gets an 'env' account from a fake environment variable.
    """
    for name, module in (("annas", annas), ("zlib", zlib)):
        stub = stubs.get(name)
        if stub is not None:
            monkeypatch.setenv("ANNAS_KEY" if name == "annas" else "ZLIB_EMAIL", "x")

        def from_account(cls, account, config, browser=True, stub=stub):
            if isinstance(stub, Exception):
                raise stub
            return stub

        monkeypatch.setattr(module.Source, "from_account", classmethod(from_account))


ROW_A = result("annas", "0" * 32, title="Book A", author="Author", extension="epub", size="1MB", language="English [en]", year="1900")
ROW_Z = result("zlib", "12/ab", title="Book Z", author="Author", publisher="Pub", extension="epub", size="398 KB", language="English")


def test_search_all_one_source_failing(monkeypatch, capsys):
    a = StubSource("annas", error=annas.AnnasError("GET /search failed: HTTP 503"))
    z = StubSource("zlib", rows=[ROW_Z])
    install(monkeypatch, annas=a, zlib=z)
    assert cli.main(["search", "book", "--ext", "epub", "--lang", "en", "--limit", "3"]) == 0
    out, err = capsys.readouterr()
    assert out == "1. Book Z\n   Author | Pub\n   English · EPUB · 398 KB\n   id zlib:12/ab\n"
    assert "bookfetch: annas: GET /search failed: HTTP 503" in err
    assert z.calls == [("search", "book", "epub", "en", 3)]
    assert a.closed and z.closed


def test_search_all_skips_unconfigured(monkeypatch, capsys):
    install(monkeypatch, annas=StubSource("annas", rows=[ROW_A]))
    assert cli.main(["search", "book", "--json"]) == 0
    out, err = capsys.readouterr()
    assert [r["id"] for r in json.loads(out)] == ["0" * 32]
    assert "zlib: skipped, not configured" in err


def test_search_merges_sources_in_order(monkeypatch, capsys):
    install(monkeypatch, annas=StubSource("annas", rows=[ROW_A]), zlib=StubSource("zlib", rows=[ROW_Z]))
    assert cli.main(["search", "book"]) == 0
    out = capsys.readouterr().out
    assert out.index("1. Book A") < out.index("2. Book Z")
    assert "id annas:" + "0" * 32 in out


def test_search_single_source(monkeypatch, capsys):
    a = StubSource("annas", rows=[ROW_A])
    z = StubSource("zlib", rows=[ROW_Z])
    install(monkeypatch, annas=a, zlib=z)
    assert cli.main(["search", "book", "--source", "zlib"]) == 0
    assert a.calls == []
    assert "Book Z" in capsys.readouterr().out


def test_search_all_failing_exit_1(monkeypatch, capsys):
    install(monkeypatch, zlib=StubSource("zlib", error=ZlibError("search: blocked")))
    assert cli.main(["search", "book"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "zlib: search: blocked" in err


def test_nothing_configured_exit_2(monkeypatch, capsys):
    install(monkeypatch)
    assert cli.main(["search", "book"]) == 2
    assert cli.main(["search", "book", "--source", "annas"]) == 2
    assert "annas: no key: set ANNAS_KEY" in capsys.readouterr().err


def test_no_results(monkeypatch, capsys):
    install(monkeypatch, zlib=StubSource("zlib"))
    assert cli.main(["search", "nothing"]) == 0
    assert capsys.readouterr().out == "no results\n"


def test_download_routes_by_prefix(monkeypatch, tmp_path, capsys):
    a = StubSource("annas")
    z = StubSource("zlib")
    install(monkeypatch, annas=a, zlib=z)
    assert cli.main(["download", "zlib:12/ab", "--out", str(tmp_path), "--name", "x.epub"]) == 0
    assert z.calls == [("download", "12/ab", str(tmp_path), "x.epub")]
    assert a.calls == []
    assert capsys.readouterr().out == f"{tmp_path / 'x.epub'} (2 bytes); zlib downloads left today: 9\n"
    assert z.closed


@pytest.mark.parametrize("target", ["12/ab", "gutenberg:1", "zlib:", ":abc"])
def test_download_bad_target(monkeypatch, capsys, target):
    install(monkeypatch)
    with pytest.raises(SystemExit) as e:
        cli.main(["download", target])
    assert e.value.code == 2
    assert "expected SOURCE:ID" in capsys.readouterr().err


def test_parse_target_keeps_colons_in_id():
    assert cli.parse_target("zlib:12/ab") == ("zlib", "12/ab")
    assert cli.parse_target("annas: " + "f" * 32) == ("annas", "f" * 32)


def test_quota_all(monkeypatch, capsys):
    install(monkeypatch, annas=StubSource("annas", error=annas.AnnasError("login failed: x")), zlib=StubSource("zlib"))
    assert cli.main(["quota"]) == 0
    out, err = capsys.readouterr()
    assert out == "annas/env: error: login failed: x\nzlib/env: downloads today: 1, downloads limit: 10\n"
    assert err == ""


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == "bookfetch 0.3.0"
