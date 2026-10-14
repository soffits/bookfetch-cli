import json
import os

import pytest

from bookfetch import cli, config
from bookfetch.common import ConfigError, MissingConfigError
from bookfetch.sources import annas, zlib
from bookfetch.sources.annas import AnnasError, NoDownloadsLeftError
from bookfetch.sources.zlib import Client, Credentials, ZlibError

SECRET_KEY = "annas-SECRET-key-123"
SECRET_PW = "zlib-SECRET-pw-456"

CONFIG = f"""
[annas.accounts.main]
key_file = "~/keys/main"

[annas.accounts.spare]
key = "{SECRET_KEY}"

[zlib.accounts.me]
email = "me@example.com"
password = "{SECRET_PW}"

[zlib.accounts.other]
email = "other@example.com"
password_file = "~/keys/other-pw"

[annas]
domains = ["a.example"]
"""


@pytest.fixture(autouse=True)
def reset_warnings():
    config._warned.clear()


def write(tmp_path, text=CONFIG, mode=0o600, name="config.toml"):
    path = tmp_path / name
    path.write_text(text)
    path.chmod(mode)
    return path


def load(path=None, env=None, warn=None):
    return config.load(str(path) if path else None, env=env or {}, warn=warn or (lambda m: None))


# --- config file parsing and precedence ---------------------------------------


def test_file_accounts_in_order_with_tilde_expanded(tmp_path):
    cfg = load(write(tmp_path), env={"HOME": "/home/reader"})
    assert [a.name for a in cfg.accounts["annas"]] == ["main", "spare"]
    assert [a.name for a in cfg.accounts["zlib"]] == ["me", "other"]
    assert cfg.accounts["annas"][0].fields["key_file"] == os.path.expanduser("~/keys/main")
    assert cfg.domains("annas") == ["a.example"]
    assert cfg.domains("zlib") == zlib.DEFAULT_DOMAINS


def test_env_account_comes_first(tmp_path):
    cfg = load(write(tmp_path), env={"ANNAS_KEY": "k", "ZLIB_EMAIL": "e@example.com", "ZLIB_PASSWORD": "p"})
    assert [a.label for a in cfg.accounts["annas"]] == ["annas/env", "annas/main", "annas/spare"]
    assert [a.label for a in cfg.accounts["zlib"]] == ["zlib/env", "zlib/me", "zlib/other"]
    assert cfg.accounts["annas"][0].origin == "environment (ANNAS_KEY)"


def test_env_domains_beat_file_domains(tmp_path):
    cfg = load(write(tmp_path), env={"ANNAS_DOMAINS": "b.example"})
    assert cfg.domains("annas") == ["b.example"]


def test_browser_only_env_account_kept_only_without_other_accounts(tmp_path):
    env = {"BOOKFETCH_CDP_URL": "http://127.0.0.1:9222"}
    assert [a.name for a in load(env=env).accounts["zlib"]] == ["env"]
    assert [a.name for a in load(write(tmp_path), env=env).accounts["zlib"]] == ["me", "other"]


def test_path_precedence(tmp_path):
    flag = write(tmp_path, "[annas.accounts.flag]\nkey = 'a'\n", name="flag.toml")
    envfile = write(tmp_path, "[annas.accounts.envfile]\nkey = 'a'\n", name="env.toml")
    xdg = tmp_path / "xdg" / "bookfetch"
    xdg.mkdir(parents=True)
    write(xdg, "[annas.accounts.xdg]\nkey = 'a'\n")
    env = {"BOOKFETCH_CONFIG": str(envfile), "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    assert [a.name for a in load(flag, env=env).accounts["annas"]] == ["flag"]
    assert [a.name for a in load(env=env).accounts["annas"]] == ["envfile"]
    assert [a.name for a in load(env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")}).accounts["annas"]] == ["xdg"]
    home = {"HOME": str(tmp_path / "home")}
    assert config.resolve_path(None, home) == (tmp_path / "home" / ".config" / "bookfetch" / "config.toml", False)


def test_missing_default_file_is_fine_but_explicit_is_not(tmp_path):
    cfg = load(env={"XDG_CONFIG_HOME": str(tmp_path)})
    assert not cfg.found and cfg.accounts == {"annas": [], "zlib": []}
    with pytest.raises(ConfigError, match="config file not found"):
        load(tmp_path / "nope.toml")


@pytest.mark.parametrize(
    "text, message",
    [
        ("[gutenberg.accounts.x]\nkey = 'a'\n", "unknown source"),
        ("[annas.accounts.x]\npassword = 'a'\n", "unknown setting 'password'"),
        ("[annas.accounts.env]\nkey = 'a'\n", "reserved"),
        ("[annas]\ndomains = 'a.example'\n", "list of strings"),
        ("[annas]\ncolour = 1\n", "unknown setting annas.colour"),
        ("[annas.accounts.x]\nkey = 1\n", "must be a string"),
        ("[annas.accounts\n", "not valid TOML"),
    ],
)
def test_config_errors(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, text))


def test_secret_from_file_and_inline(tmp_path):
    (tmp_path / "pw").write_text("from-file\n")
    text = f"[zlib.accounts.a]\nemail = 'a@example.com'\npassword_file = '{tmp_path / 'pw'}'\n"
    acc = load(write(tmp_path, text)).accounts["zlib"][0]
    assert zlib.account_credentials(acc).password == "from-file"
    acc = load(write(tmp_path)).accounts["zlib"][0]
    assert zlib.account_credentials(acc).password == SECRET_PW
    acc = load(write(tmp_path, "[annas.accounts.a]\nkey_file = '/nonexistent/key'\n")).accounts["annas"][0]
    with pytest.raises(MissingConfigError, match="cannot read key file"):
        annas.account_key(acc)


# --- permission warning ---------------------------------------------------------


def test_warns_once_about_readable_file_with_inline_secrets(tmp_path):
    warnings = []
    path = write(tmp_path, mode=0o644)
    load(path, warn=warnings.append)
    load(path, warn=warnings.append)
    assert len(warnings) == 1
    assert "readable by other users" in warnings[0] and "chmod 600" in warnings[0]
    assert SECRET_KEY not in warnings[0]


def test_no_warning_for_private_file_or_file_paths_only(tmp_path):
    warnings = []
    load(write(tmp_path, mode=0o600), warn=warnings.append)
    load(write(tmp_path, "[annas.accounts.a]\nkey_file = '~/k'\n", mode=0o644, name="paths.toml"), warn=warnings.append)
    assert warnings == []


# --- secrets stay out of output -------------------------------------------------


def test_secrets_never_in_repr_or_accounts_output(tmp_path, monkeypatch, capsys):
    path = write(tmp_path)
    monkeypatch.setenv("ANNAS_KEY", "ENV-SECRET-789")
    monkeypatch.setenv("ZLIB_EMAIL", "env@example.com")
    monkeypatch.setenv("ZLIB_PASSWORD", "ENV-PW-000")
    cfg = load(path, env=dict(os.environ))
    texts = [repr(cfg), *(repr(a) for accs in cfg.accounts.values() for a in accs)]
    texts += [a.describe() for accs in cfg.accounts.values() for a in accs]
    texts.append(repr(zlib.account_credentials(cfg.accounts["zlib"][1])))
    texts.append(repr(annas.Session(SECRET_KEY, domain_list=["a.example"])))
    assert cli.main(["--config", str(path), "accounts"]) == 0
    out, err = capsys.readouterr()
    texts += [out, err]
    for secret in (SECRET_KEY, SECRET_PW, "ENV-SECRET-789", "ENV-PW-000"):
        assert not any(secret in t for t in texts), secret
    assert "  env: from environment (ANNAS_KEY); key set\n" in out
    assert "  spare: from " + str(path) + "; key set\n" in out
    assert "  me: from " + str(path) + "; email me@example.com, password set\n" in out


def test_accounts_command_lists_sources(tmp_path, capsys):
    path = write(tmp_path)
    assert cli.main(["accounts", "--config", str(path), "--source", "zlib"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"config file: {path}"
    assert "zlib: domains z-lib.sk" in out and "annas:" not in out
    assert [line.split(":")[0].strip() for line in out.splitlines()[2:]] == ["me", "other"]


def test_accounts_command_without_anything(capsys, tmp_path):
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "(not found; optional)" in out
    assert out.count("  no accounts") == 2


# --- fakes for the CLI ------------------------------------------------------------


class FakeSource:
    """Behaviour per account name: 'ok', or an exception to raise."""

    def __init__(self, source, account, behaviour, log):
        self.source, self.account, self.behaviour, self.log = source, account, behaviour, log
        self.downloads_left = 4

    def _run(self, what, value):
        self.log.append((self.account.label, what))
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return value

    def search(self, query, ext=None, lang=None, limit=10):
        return self._run("search", [{"source": self.source, "id": self.account.name, "title": "T", "author": "", "publisher": "",
                                     "language": "", "extension": "epub", "size": "", "year": "", "extra": {}}])  # fmt: skip

    def download(self, id, out=".", name=None):
        path = self._run("download", None) or (os.path.join(out, "book.epub"))
        from pathlib import Path

        Path(path).write_bytes(b"PK")
        return Path(path)

    def quota(self):
        return self._run("quota", {"downloads_today": 1, "downloads_limit": 10})

    def close(self):
        pass


def fake_accounts(monkeypatch, tmp_path, behaviours):
    """behaviours: {"annas": {"a1": "ok" | Exception, ...}, "zlib": {...}} -> config path and call log."""
    lines = []
    for source, accounts in behaviours.items():
        for name in accounts:
            lines.append(f"[{source}.accounts.{name}]\n" + ("key = 'k'\n" if source == "annas" else "email = 'e'\npassword = 'p'\n"))
    path = write(tmp_path, "\n".join(lines))
    log = []
    for source, module in (("annas", annas), ("zlib", zlib)):

        def from_account(cls, account, cfg, browser=True, source=source):
            behaviour = behaviours[source][account.name]
            if isinstance(behaviour, MissingConfigError):
                raise behaviour
            return FakeSource(source, account, behaviour, log)

        monkeypatch.setattr(module.Source, "from_account", classmethod(from_account))
    return path, log


# --- account selection ------------------------------------------------------------


def test_search_uses_first_account_only(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": "ok", "a2": "ok"}, "zlib": {"z1": "ok"}})
    assert cli.main(["--config", str(path), "search", "q", "--json"]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == ["a1", "z1"]
    assert log == [("annas/a1", "search"), ("zlib/z1", "search")]


def test_account_flag_selects_and_applies_to_matching_source(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": "ok", "a2": "ok"}, "zlib": {"z1": "ok"}})
    assert cli.main(["search", "q", "--config", str(path), "--account", "a2"]) == 0
    assert log == [("annas/a2", "search")]
    assert "zlib: skipped, no account named 'a2'" in capsys.readouterr().err


def test_unknown_account_is_an_error(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": "ok"}, "zlib": {"z1": "ok"}})
    assert cli.main(["--config", str(path), "--account", "nope", "search", "q"]) == 2
    assert cli.main(["--config", str(path), "--account", "z1", "search", "q", "--source", "annas"]) == 2
    err = capsys.readouterr().err
    assert "no account named 'nope' for annas or zlib (configured: annas/a1, zlib/z1)" in err
    assert "no account named 'z1' for annas (configured: annas/a1)" in err
    assert log == []


# --- fallback ----------------------------------------------------------------------


def test_search_falls_back_when_login_fails(monkeypatch, tmp_path, capsys):
    bad = annas.AnnasLoginError("login failed: a.example: key rejected")
    path, log = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": bad, "a2": "ok", "a3": "ok"}})
    assert cli.main(["--config", str(path), "search", "q", "--source", "annas", "--json"]) == 0
    out, err = capsys.readouterr()
    assert [r["id"] for r in json.loads(out)] == ["a2"]
    assert "annas/a1: login failed: a.example: key rejected; trying annas/a2" in err
    assert log == [("annas/a1", "search"), ("annas/a2", "search")]


def test_search_falls_back_on_unreadable_secret(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"zlib": {"z1": MissingConfigError("cannot read password file x"), "z2": "ok"}})
    assert cli.main(["--config", str(path), "search", "q"]) == 0
    assert "zlib/z1: cannot read password file x; trying zlib/z2" in capsys.readouterr().err


def test_search_other_errors_do_not_fall_back(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": AnnasError("GET /search failed: HTTP 503"), "a2": "ok"}})
    assert cli.main(["--config", str(path), "search", "q"]) == 1
    assert log == [("annas/a1", "search")]
    assert "bookfetch: annas/a1: GET /search failed: HTTP 503" in capsys.readouterr().err


def test_download_quota_fallback_annas(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": NoDownloadsLeftError("API error: No downloads left"), "a2": "ok"}})
    assert cli.main(["--config", str(path), "download", "annas:" + "a" * 32, "--out", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert "annas/a1: API error: No downloads left; trying annas/a2" in err
    assert out == f"{tmp_path / 'book.epub'} (2 bytes); annas/a2 downloads left today: 4\n"
    assert log == [("annas/a1", "download"), ("annas/a2", "download")]


def test_download_all_accounts_exhausted_tries_each_once(monkeypatch, tmp_path, capsys):
    quota = zlib.ZlibQuotaError("no downloads left today (10 of 10 used)")
    path, log = fake_accounts(monkeypatch, tmp_path, {"zlib": {"z1": quota, "z2": quota}})
    assert cli.main(["--config", str(path), "download", "zlib:1/ab", "--out", str(tmp_path)]) == 1
    assert log == [("zlib/z1", "download"), ("zlib/z2", "download")]
    assert "zlib/z2: no downloads left today" in capsys.readouterr().err


def test_download_with_account_flag_does_not_fall_back(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": NoDownloadsLeftError("API error: No downloads left"), "a2": "ok"}})
    assert cli.main(["--config", str(path), "--account", "a1", "download", "annas:" + "a" * 32, "--out", str(tmp_path)]) == 1
    assert log == [("annas/a1", "download")]


def test_download_other_error_does_not_fall_back(monkeypatch, tmp_path, capsys):
    path, log = fake_accounts(monkeypatch, tmp_path, {"zlib": {"z1": ZlibError("download failed: timeout"), "z2": "ok"}})
    assert cli.main(["--config", str(path), "download", "zlib:1/ab", "--out", str(tmp_path)]) == 1
    assert log == [("zlib/z1", "download")]


def test_quota_lists_every_account_with_one_failing(monkeypatch, tmp_path, capsys):
    bad = annas.AnnasLoginError("login failed: a.example: key rejected")
    path, _ = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": "ok", "a2": bad}, "zlib": {"z1": "ok"}})
    assert cli.main(["--config", str(path), "quota"]) == 0
    assert capsys.readouterr().out == (
        "annas/a1: downloads today: 1, downloads limit: 10\n"
        "annas/a2: error: login failed: a.example: key rejected\n"
        "zlib/z1: downloads today: 1, downloads limit: 10\n"
    )


def test_quota_all_failing_exit_1(monkeypatch, tmp_path, capsys):
    path, _ = fake_accounts(monkeypatch, tmp_path, {"annas": {"a1": MissingConfigError("cannot read key file /x")}})
    assert cli.main(["--config", str(path), "quota", "--source", "annas"]) == 1
    assert capsys.readouterr().out == "annas/a1: error: cannot read key file /x\n"


# --- the real sources detect exhausted quota -----------------------------------------


class FakeTransport:
    def __init__(self, routes, browser=False):
        self.routes, self.browser, self.calls = routes, browser, []

    def request(self, method, path, form=None):
        self.calls.append((method, path))
        return self.routes[(method, path)]

    def close(self):
        pass


def zlib_source(profile, file_response=None, browser=False, email="me@example.com"):
    routes = {
        ("POST", "/eapi/user/login"): (200, json.dumps({"success": 1})),
        ("GET", "/eapi/user/profile"): (200, json.dumps({"success": 1, "user": profile})),
        ("GET", "/eapi/book/1/ab/file"): file_response or (200, json.dumps({"success": 1, "file": {}})),
    }
    t = FakeTransport(routes, browser=browser)
    return zlib.Source(Client(t, Credentials(email, "pw"))), t


def test_zlib_download_checks_quota_first(tmp_path):
    s, t = zlib_source({"email": "me@example.com", "downloads_today": 10, "downloads_limit": 10})
    with pytest.raises(zlib.ZlibQuotaError, match="10 of 10 used"):
        s.download("1/ab", out=tmp_path)
    assert ("GET", "/eapi/book/1/ab/file") not in t.calls


def test_zlib_limit_error_is_quota_error(tmp_path):
    limit = (200, json.dumps({"success": 0, "error": "Daily limit reached"}))
    s, _ = zlib_source({"downloads_today": 3, "downloads_limit": 10}, file_response=limit)
    with pytest.raises(zlib.ZlibQuotaError, match="limit"):
        s.download("1/ab", out=tmp_path)


def test_zlib_browser_session_of_another_account_logs_in_again():
    s, t = zlib_source({"email": "someone@example.com", "downloads_today": 0, "downloads_limit": 10}, browser=True)
    s.quota()
    assert ("POST", "/eapi/user/login") in t.calls
    s, t = zlib_source({"email": "Me@Example.com", "downloads_today": 0, "downloads_limit": 10}, browser=True)
    s.quota()
    assert ("POST", "/eapi/user/login") not in t.calls


def test_zlib_login_rejected_is_login_error():
    s, t = zlib_source({})
    t.routes[("POST", "/eapi/user/login")] = (200, json.dumps({"success": 0, "error": "Incorrect email or password"}))
    with pytest.raises(zlib.ZlibLoginError, match="Incorrect"):
        s.quota()


@pytest.mark.parametrize(
    "error, cls",
    [("No downloads left", NoDownloadsLeftError), ("Invalid secret key", annas.AnnasLoginError), ("Not a member", annas.AnnasLoginError)],
)
def test_annas_api_errors_are_classified(error, cls):
    from test_annas_api import session

    s, _ = session({"fast_download.json": json.dumps({"download_url": None, "error": error}).encode()})
    with pytest.raises(cls, match=error):
        s.fast_download_info("0" * 32)


def test_quota_uses_plain_http_for_zlib_accounts_with_credentials(tmp_path):
    cfg = load(write(tmp_path), env={"BOOKFETCH_CDP_URL": "http://127.0.0.1:9"})
    me = cfg.accounts["zlib"][0]
    assert isinstance(zlib.Source.from_account(me, cfg, browser=False).client.transport, zlib.HttpTransport)
    assert isinstance(zlib.Source.from_account(me, cfg).client.transport, zlib.BrowserTransport)
