"""Shared parts of the open sources: no account needed, polite HTTP, no download quota."""

from __future__ import annotations

from collections.abc import Mapping

from ..common import Account, MissingConfigError
from ..net import Http

ANONYMOUS = "anonymous"


class OpenSource:
    """Base for sources that work without an account.

    Subclasses set ``name`` and ``limits`` and implement ``make_http``,
    ``search`` and ``download``. After a download the CLI prints
    ``downloads_note`` instead of a "downloads left today" count.
    """

    name = ""
    limits = "no account quota"

    def __init__(self, http: Http | None = None, account: Account | None = None):
        self.account = account
        self.http = http or self.make_http()
        self.downloads_left = None
        self.downloads_note = "no download quota"

    def make_http(self) -> Http:
        raise NotImplementedError

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        return cls()

    @classmethod
    def from_account(cls, account: Account, config, browser: bool = True):
        if account.name != ANONYMOUS:
            raise MissingConfigError(f"{account.label}: {cls.name} has no accounts; it works without one")
        return cls(account=account)

    def quota(self) -> dict:
        return {"account": "none needed", "limits": self.limits}

    def close(self) -> None:
        pass


def no_account(env: Mapping[str, str] | None = None) -> Account | None:
    """``env_account`` for sources without accounts."""
    return None


def limit_rows(rows: list[dict], limit: int) -> list[dict]:
    return rows[: max(limit, 0)]
