"""Offline tests: The open sources in the account list and the CLI."""

import json
from pathlib import Path

import pytest
from helpers import EPUB

from bookfetch import cli, config
from bookfetch.common import ConfigError, result
from bookfetch.sources import arxiv, gutenberg, openlibrary, standardebooks


def load(tmp_path, text="", env=None):
    path = tmp_path / "config.toml"
    path.write_text(text)
    path.chmod(0o600)
    return config.load(str(path), env=env or {}, warn=lambda m: None)


@pytest.mark.open_sources
def test_open_sources_get_builtin_anonymous_account(tmp_path):
    cfg = load(tmp_path)
    for name in ("gutenberg", "standardebooks", "openlibrary", "arxiv"):
        assert [a.name for a in cfg.accounts[name]] == ["anonymous"]
    assert cfg.accounts["annas"] == [] and cfg.accounts["zlib"] == []


@pytest.mark.open_sources
def test_standardebooks_patrons_first_then_anonymous(tmp_path):
    secret = "me@example.com"
    cfg = load(tmp_path, f'[standardebooks.accounts.me]\nemail = "{secret}"\n', env={"STANDARDEBOOKS_EMAIL": "env@example.com"})
    accounts = cfg.accounts["standardebooks"]
    assert [a.name for a in accounts] == ["env", "me", "anonymous"]
    assert all(secret not in a.describe() and "env@example.com" not in a.describe() for a in accounts)
    source = standardebooks.Source.from_account(accounts[1], cfg)
    assert source.email == secret
    assert standardebooks.Source.from_account(accounts[2], cfg).email == ""


@pytest.mark.open_sources
@pytest.mark.parametrize(
    "text, message",
    [
        ("[gutenberg.accounts.me]\nemail = 'x'\n", "has no accounts"),
        ("[arxiv]\ndomains = ['x']\n", "unknown setting"),
        ("[standardebooks.accounts.anonymous]\nemail = 'x'\n", "reserved"),
        ("[standardebooks.accounts.me]\npassword = 'x'\n", "unknown setting"),
    ],
)
def test_open_source_config_errors(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, text)


class Stub:
    def __init__(self, name, rows=None, error=None):
        self.name, self.rows, self.error = name, rows or [], error
        self.downloads_left = None
        self.downloads_note = "no download quota"

    def search(self, query, ext=None, lang=None, limit=10):
        if self.error:
            raise self.error
        self.limit = limit
        return [r for r in self.rows][:limit]

    def download(self, id, out=".", name=None):
        p = Path(out) / "book.epub"
        p.write_bytes(EPUB)
        return p

    def quota(self):
        return {"account": "none needed", "limits": "polite"}

    def close(self):
        pass


def install_open(monkeypatch, stubs):
    for module in (gutenberg, standardebooks, openlibrary, arxiv):
        stub = stubs.get(module.NAME) or Stub(module.NAME)
        monkeypatch.setattr(module.Source, "from_account", classmethod(lambda cls, account, cfg, browser=True, stub=stub: stub))


def row(source, ident):
    return result(source, ident, title=f"{source} book", extension="epub")


@pytest.mark.open_sources
def test_search_all_includes_open_sources_without_config(monkeypatch, capsys):
    stubs = {
        "gutenberg": Stub("gutenberg", [row("gutenberg", "1342")]),
        "arxiv": Stub("arxiv", error=arxiv.ArxivError("arXiv refused the request")),
        "openlibrary": Stub("openlibrary", [row("openlibrary", "x")]),
    }
    install_open(monkeypatch, stubs)
    assert cli.main(["search", "pride", "--json"]) == 0
    out, err = capsys.readouterr()
    assert [r["source"] for r in json.loads(out)] == ["gutenberg", "openlibrary"]
    assert "annas: skipped" in err and "zlib: skipped" in err
    assert "arxiv: arXiv refused the request" in err
    assert stubs["gutenberg"].limit == cli.DEFAULT_LIMIT_ALL


@pytest.mark.open_sources
def test_single_source_default_limit(monkeypatch, capsys):
    stubs = {"gutenberg": Stub("gutenberg", [row("gutenberg", "1342")])}
    install_open(monkeypatch, stubs)
    assert cli.main(["search", "pride", "--source", "gutenberg"]) == 0
    assert stubs["gutenberg"].limit == cli.DEFAULT_LIMIT_ONE
    assert "id gutenberg:1342" in capsys.readouterr().out


@pytest.mark.open_sources
def test_source_list_uses_all_sources_limit(monkeypatch, capsys):
    stubs = {"gutenberg": Stub("gutenberg", [row("gutenberg", "1342")]), "arxiv": Stub("arxiv", [])}
    install_open(monkeypatch, stubs)
    assert cli.main(["search", "pride", "--source", "gutenberg,arxiv"]) == 0
    assert stubs["gutenberg"].limit == cli.DEFAULT_LIMIT_ALL
    assert stubs["arxiv"].limit == cli.DEFAULT_LIMIT_ALL


@pytest.mark.open_sources
def test_download_open_source_prints_no_quota(monkeypatch, tmp_path, capsys):
    install_open(monkeypatch, {})
    assert cli.main(["download", "arxiv:hep-th/9901001v3", "--out", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip().endswith("; arxiv: no download quota")


@pytest.mark.open_sources
def test_accounts_and_quota_list_open_sources(capsys):
    assert cli.main(["accounts", "--source", "gutenberg"]) == 0
    assert "anonymous: built in; no login" in capsys.readouterr().out
    assert cli.main(["quota", "--source", "arxiv"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("arxiv/anonymous: account: none needed, limits: no account quota; arXiv allows 1 API request every 3 s")
    assert cli.main(["quota", "--source", "standardebooks"]) == 0
    assert "35 downloads per 30 s" in capsys.readouterr().out
