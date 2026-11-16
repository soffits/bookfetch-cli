import urllib.request

from bookfetch.common import source_opener


def proxies(opener):
    handler = next((h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)), None)
    return handler.proxies if handler else {}


def test_source_proxy_env_applies_to_that_source_only(monkeypatch):
    monkeypatch.setenv("ZLIB_PROXY", "http://127.0.0.1:40081")
    monkeypatch.delenv("ANNAS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    assert proxies(source_opener("ZLIB"))["https"] == "http://127.0.0.1:40081"
    assert "https" not in proxies(source_opener("ANNAS"))
