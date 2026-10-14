"""Offline tests: arXiv."""

import pytest
from helpers import PDF, fixture, make_http

from bookfetch import cli, net
from bookfetch.sources import arxiv


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1706.03762", "1706.03762"),
        ("1706.03762v7", "1706.03762v7"),
        ("arXiv:1706.03762", "1706.03762"),
        ("hep-th/9901001", "hep-th/9901001"),
        ("hep-th/9901001v3", "hep-th/9901001v3"),
        ("math.GT/0309136", "math.GT/0309136"),
        ("https://arxiv.org/abs/hep-th/9901001v3", "hep-th/9901001v3"),
        ("https://arxiv.org/pdf/1706.03762v7.pdf", "1706.03762v7"),
    ],
)
def test_arxiv_ids(value, expected):
    assert arxiv.parse_id(value) == expected


@pytest.mark.parametrize("value", ["", "1706", "hep-th/99", "../etc/passwd", "1706.03762/../x"])
def test_arxiv_bad_ids(value):
    with pytest.raises(arxiv.ArxivError):
        arxiv.parse_id(value)


@pytest.mark.open_sources
def test_old_arxiv_id_survives_source_id_parsing():
    assert cli.parse_target("arxiv:hep-th/9901001v3") == ("arxiv", "hep-th/9901001v3")


def test_arxiv_queries():
    assert arxiv.build_queries("hep-th/9901001") == [{"id_list": "hep-th/9901001"}]
    assert arxiv.build_queries("au:hinton AND ti:boltzmann") == [{"search_query": "au:hinton AND ti:boltzmann"}]
    assert arxiv.build_queries("Attention is all you need") == [
        {"search_query": 'ti:"attention is all you need"'},
        {"search_query": "all:attention AND all:need"},
    ]


def test_arxiv_parse_search():
    rows = arxiv.parse_search(fixture("arxiv_search.atom.xml"))
    assert [r["id"] for r in rows] == ["2101.00001v2", "hep-th/9901001v3"]
    assert rows[0]["title"] == "An Example Paper About Attention and Other Things"
    assert rows[0]["author"] == "Ada Example, Ben Example, Cy Example et al."
    assert rows[0]["publisher"] == "arXiv" and rows[0]["year"] == "2021" and rows[0]["extension"] == "pdf"
    assert rows[1]["publisher"] == "Example J. Phys. 1:1-2, 1999" and rows[1]["extra"]["doi"] == "10.0000/example.1"


def test_arxiv_search_falls_back_to_words_and_keeps_3s_gap():
    routes = {
        "https://export.arxiv.org/api/query?search_query=ti%3A%22some+paper%22*": (200, fixture("arxiv_empty.atom.xml")),
        "https://export.arxiv.org/api/query?search_query=all%3Asome+AND+all%3Apaper*": (200, fixture("arxiv_search.atom.xml")),
    }
    http, web, clock = make_http(arxiv, routes)
    rows = arxiv.Source(http=http).search("some paper", limit=1)
    assert [r["id"] for r in rows] == ["2101.00001v2"]
    assert len(web.requests) == 2 and clock.slept == [3.0]
    assert arxiv.Source(http=http).search("x", ext="epub") == []


def test_arxiv_download_old_style_id(tmp_path):
    routes = {"https://arxiv.org/pdf/hep-th/9901001v3": (200, PDF, {"Content-Type": "application/pdf"})}
    http, _, _ = make_http(arxiv, routes)
    path = arxiv.Source(http=http).download("hep-th/9901001v3", out=tmp_path)
    assert path.name == "hep-th_9901001v3.pdf" and path.read_bytes() == PDF


def test_arxiv_403_stops_with_clear_message(tmp_path):
    http, web, _ = make_http(arxiv, {"https://arxiv.org/pdf/1706.03762": (403, b"denied", {"Content-Type": "text/plain"})})
    with pytest.raises(arxiv.ArxivError, match="blocked"):
        arxiv.Source(http=http).download("1706.03762", out=tmp_path)
    assert len(web.requests) == 1


def test_arxiv_refuses_non_pdf(tmp_path):
    http, _, _ = make_http(
        arxiv, {"https://arxiv.org/pdf/1706.03762": (200, b"\x00\x01binary", {"Content-Type": "application/octet-stream"})}
    )
    with pytest.raises(net.HttpError, match="expected type"):
        arxiv.Source(http=http).download("1706.03762", out=tmp_path)
