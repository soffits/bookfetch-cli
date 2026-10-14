import pytest

ENV_VARS = (
    "ANNAS_KEY",
    "ANNAS_KEY_FILE",
    "ANNAS_DOMAINS",
    "ZLIB_EMAIL",
    "ZLIB_PASSWORD",
    "ZLIB_PASSWORD_FILE",
    "ZLIB_DOMAINS",
    "BOOKFETCH_CDP_URL",
    "BOOKFETCH_CONFIG",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Keep the user's real accounts and config file out of every test."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
