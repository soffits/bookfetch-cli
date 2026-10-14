import socket

import pytest

from bookfetch.sources import SOURCES

ENV_VARS = (
    "ANNAS_KEY",
    "ANNAS_KEY_FILE",
    "ANNAS_DOMAINS",
    "ZLIB_EMAIL",
    "ZLIB_PASSWORD",
    "ZLIB_PASSWORD_FILE",
    "ZLIB_DOMAINS",
    "STANDARDEBOOKS_EMAIL",
    "BOOKFETCH_CDP_URL",
    "BOOKFETCH_CONFIG",
)
OPEN_SOURCES = ("gutenberg", "standardebooks", "openlibrary", "arxiv")


def pytest_configure(config):
    config.addinivalue_line("markers", "open_sources: include the open sources (Gutenberg, Standard Ebooks, Open Library, arXiv)")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Keep the user's real accounts, config file and download log out of every test."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach the real sites; only loopback connections (the fake CDP server) are allowed."""
    real_connect = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"test tried to reach the network: {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture(autouse=True)
def only_marked_open_sources(request):
    """Tests written for the account sources see only annas and zlib, unless marked open_sources."""
    if request.node.get_closest_marker("open_sources") is not None:
        yield
        return
    saved = dict(SOURCES)
    for name in OPEN_SOURCES:
        SOURCES.pop(name, None)
    yield
    SOURCES.clear()
    SOURCES.update(saved)  # keeps the original order
