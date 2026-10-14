"""Accounts and settings: environment variables plus an optional TOML config file.

The environment variables define one account per source, named ``env``, which
comes first. The config file adds more accounts, in file order::

    [annas.accounts.main]
    key_file = "~/.secrets/annas-key"

    [zlib.accounts.me]
    email = "a@example.com"
    password_file = "~/.secrets/zlib-password"

    [zlib]
    domains = ["z-lib.sk"]
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from .common import Account, ConfigError, env_list
from .sources import SOURCES

ENV_ACCOUNT = "env"
# Sources that work without an account get a built-in account with this name,
# used after any configured ones.
ANONYMOUS_ACCOUNT = "anonymous"
_warned: set[str] = set()


def default_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME", "").strip() or os.path.join(env.get("HOME", "").strip() or str(Path.home()), ".config")
    return Path(base) / "bookfetch" / "config.toml"


def resolve_path(flag: str | None, env: Mapping[str, str]) -> tuple[Path, bool]:
    """The config path and whether it was asked for explicitly (--config or BOOKFETCH_CONFIG)."""
    explicit = flag or env.get("BOOKFETCH_CONFIG", "").strip()
    if explicit:
        return Path(explicit).expanduser(), True
    return default_path(env), False


class Config:
    def __init__(self, path: Path, found: bool, env: Mapping[str, str]):
        self.path = path
        self.found = found
        self.env = env
        self.accounts: dict[str, list[Account]] = {name: [] for name in SOURCES}
        self.file_domains: dict[str, list[str]] = {}

    def __repr__(self) -> str:
        return f"Config(path={str(self.path)!r}, accounts={[a.label for accs in self.accounts.values() for a in accs]})"

    def domains(self, source: str) -> list[str]:
        """Domains for a source: the environment variable, else the config file, else the built-in list."""
        module = SOURCES[source]
        return env_list(module.DOMAINS_ENV, self.file_domains.get(source) or module.DEFAULT_DOMAINS, self.env)

    def select(self, source: str, name: str | None = None) -> list[Account]:
        """All accounts of a source in order, or only the one called ``name``."""
        accounts = self.accounts[source]
        return [a for a in accounts if a.name == name] if name else list(accounts)


def _strings(value, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return [v.strip() for v in value if v.strip()]


def _parse(data: dict, path: Path) -> tuple[dict[str, list[Account]], dict[str, list[str]]]:
    accounts: dict[str, list[Account]] = {}
    domains: dict[str, list[str]] = {}
    for source, section in data.items():
        if source not in SOURCES:
            raise ConfigError(f"{path}: unknown source [{source}] (expected one of {', '.join(SOURCES)})")
        if not isinstance(section, dict):
            raise ConfigError(f"{path}: [{source}] must be a table")
        module = SOURCES[source]
        for key, value in section.items():
            if key == "domains" and module.DOMAINS_ENV:
                domains[source] = _strings(value, f"{path}: {source}.domains")
            elif key != "accounts":
                expected = "accounts or domains" if module.DOMAINS_ENV else "accounts" if module.ACCOUNT_FIELDS else "nothing"
                raise ConfigError(f"{path}: unknown setting {source}.{key} (expected {expected})")
        table = section.get("accounts", {})
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: {source}.accounts must be a table of accounts")
        if table and not module.ACCOUNT_FIELDS:
            raise ConfigError(f"{path}: {source} has no accounts; it works without one")
        for name, fields in table.items():
            where = f"{path}: [{source}.accounts.{name}]"
            if name == ENV_ACCOUNT:
                raise ConfigError(f"{where}: the name '{ENV_ACCOUNT}' is reserved for the account from environment variables")
            if name == ANONYMOUS_ACCOUNT:
                raise ConfigError(f"{where}: the name '{ANONYMOUS_ACCOUNT}' is reserved for the built-in account without login")
            if not isinstance(fields, dict):
                raise ConfigError(f"{where} must be a table")
            clean = {}
            for field, value in fields.items():
                if field not in module.ACCOUNT_FIELDS:
                    raise ConfigError(f"{where}: unknown setting {field!r} (expected {', '.join(module.ACCOUNT_FIELDS)})")
                if not isinstance(value, str):
                    raise ConfigError(f"{where}: {field} must be a string")
                clean[field] = str(Path(value.strip()).expanduser()) if field.endswith("_file") and value.strip() else value
            accounts.setdefault(source, []).append(Account(source, name, str(path), clean, module.SECRET_FIELDS))
    return accounts, domains


def _warn_permissions(path: Path, accounts: dict[str, list[Account]], warn) -> None:
    inline = any(a.fields.get(f, "").strip() for accs in accounts.values() for a in accs for f in a.secret_fields)
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if inline and mode & (stat.S_IRWXG | stat.S_IRWXO) and str(path) not in _warned:
        _warned.add(str(path))
        warn(f"warning: {path} contains passwords or keys and is readable by other users; run: chmod 600 {path}")


def load(
    flag: str | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Account] | None = None,
    warn=None,
) -> Config:
    """Read the environment and the config file.

    ``overrides`` replace a source's environment account (the --annas-key-file flag).
    A missing default config file is fine; a missing file named by --config or
    BOOKFETCH_CONFIG is an error.
    """
    env = os.environ if env is None else env
    warn = warn or (lambda msg: print(f"bookfetch: {msg}", file=sys.stderr))
    path, explicit = resolve_path(flag, env)
    try:
        raw = path.read_bytes()
        found = True
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f"config file not found: {path}") from None
        raw, found = b"", False
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e.strerror or e}") from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: not valid TOML: {e}") from None
    file_accounts, file_domains = _parse(data, path)
    if found:
        _warn_permissions(path, file_accounts, warn)

    config = Config(path, found, env)
    config.file_domains = file_domains
    for name, module in SOURCES.items():
        others = file_accounts.get(name, [])
        first = (overrides or {}).get(name) or module.env_account(env)
        # An environment account without settings (Z-Library with only a browser
        # session) is kept only when nothing else is configured for the source.
        if first and (first.fields or not others):
            config.accounts[name].append(first)
        config.accounts[name].extend(others)
        if getattr(module, "ANONYMOUS", False):
            config.accounts[name].append(Account(name, ANONYMOUS_ACCOUNT, "built in", {}))
    return config
