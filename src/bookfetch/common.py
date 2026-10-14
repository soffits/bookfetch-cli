"""Helpers shared by the sources and the CLI."""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
TIMEOUT = 60
DOWNLOAD_TIMEOUT = 600


class BookfetchError(Exception):
    """A user-facing error; the CLI prints it and exits non-zero."""


class MissingConfigError(BookfetchError):
    """A source or account is not configured (no key, no account, unreadable secret file)."""


class ConfigError(BookfetchError):
    """The config file or the account selection is invalid."""


class LoginError(BookfetchError):
    """An account could not log in or is not allowed to download (no membership)."""


class QuotaExhaustedError(BookfetchError):
    """An account has no downloads left today."""


class Account:
    """One configured account for one source.

    ``fields`` holds the source's settings as given (for example ``key_file`` or
    ``email``). Secret values never appear in ``repr`` or ``describe``.
    """

    def __init__(self, source: str, name: str, origin: str, fields: Mapping[str, str], secret_fields: tuple[str, ...] = ()):
        self.source = source
        self.name = name
        self.origin = origin
        self.fields = dict(fields)
        self.secret_fields = secret_fields

    @property
    def label(self) -> str:
        return f"{self.source}/{self.name}"

    def __repr__(self) -> str:
        return f"Account({self.label!r}, origin={self.origin!r})"

    def get(self, field: str) -> str:
        return self.fields.get(field, "").strip()

    def secret(self, field: str, what: str) -> str:
        """The secret ``field`` given inline, else read from ``<field>_file``; empty if neither is set."""
        value = self.fields.get(field, "").strip()
        if value:
            return value
        path = self.get(f"{field}_file")
        return read_secret_file(path, what) if path else ""

    def describe(self) -> str:
        """The account's settings with secret values replaced by 'set'."""
        parts = [f"{k} set" if k in self.secret_fields else f"{k} {v}" for k, v in self.fields.items() if v]
        return ", ".join(parts) or "no settings"


def read_secret_file(path: str, what: str) -> str:
    try:
        value = Path(path).expanduser().read_text().strip()
    except OSError as e:
        raise MissingConfigError(f"cannot read {what} file {path}: {e.strerror or e}") from None
    if not value:
        raise MissingConfigError(f"{what} file {path} is empty")
    return value


def env_list(name: str, default: list[str], env: Mapping[str, str] | None = None) -> list[str]:
    """A comma-separated environment variable, or ``default`` when unset or blank."""
    values = os.environ if env is None else env
    parsed = [d.strip() for d in values.get(name, "").split(",") if d.strip()]
    return parsed or list(default)


def safe_filename(name: str, fallback: str = "download") -> str:
    """Replace characters that are unsafe in file names and cap the length."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    if name in ("", ".", ".."):
        name = fallback
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = stem[: 199 - len(ext)] + dot + ext if dot and 0 < len(ext) <= 8 else name[:200]
    return name


def filename_from_url(url: str) -> str:
    return urllib.parse.unquote(url.split("/")[-1].split("?")[0])


def filename_from_disposition(value: str | None) -> str:
    """The file name from a Content-Disposition header, if any."""
    if not value:
        return ""
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", value)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]*)"', value) or re.search(r"filename\s*=\s*([^;]+)", value)
    return m.group(1).strip() if m else ""


def result(source: str, id: str, **fields) -> dict:
    """Build a search result in the common shape shared by all sources."""
    row = {"source": source, "id": id}
    for key in ("title", "author", "publisher", "year", "language", "extension", "size"):
        value = fields.pop(key, "")
        row[key] = "" if value is None else str(value)
    row["extra"] = fields.pop("extra", {})
    if fields:
        raise TypeError(f"unknown result fields: {sorted(fields)}")
    return row
