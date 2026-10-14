"""A small Atom / OPDS 1 reader shared by the Gutenberg, arXiv and Standard Ebooks sources.

It only reads data. Links are returned as data for the caller to look at; the
sources build every URL they request from fixed patterns and never follow a
link just because a page contains it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .common import BookfetchError

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}
ACQUISITION = "http://opds-spec.org/acquisition"
NOT_FREE = ("/buy", "/borrow", "/sample", "/subscribe")


class FeedError(BookfetchError):
    """The response is not a readable Atom feed."""


@dataclass
class Link:
    href: str
    rel: str = ""
    type: str = ""
    title: str = ""
    length: str = ""


@dataclass
class Entry:
    id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    content: str = ""
    published: str = ""
    updated: str = ""
    issued: str = ""
    language: str = ""
    publisher: str = ""
    links: list[Link] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)

    def acquisitions(self) -> list[Link]:
        """Free acquisition links (no buy, borrow, sample or subscribe), without data: URIs."""
        return [
            link
            for link in self.links
            if link.rel.startswith(ACQUISITION)
            and not link.rel[len(ACQUISITION) :].startswith(NOT_FREE)
            and not link.href.startswith("data:")
        ]


@dataclass
class Feed:
    title: str = ""
    total: int | None = None
    entries: list[Entry] = field(default_factory=list)


def text(value: str | None) -> str:
    """Collapse whitespace (arXiv titles contain line breaks)."""
    return re.sub(r"\s+", " ", value or "").strip()


def _find(el: ET.Element, path: str) -> str:
    found = el.find(path, NS)
    return text("".join(found.itertext())) if found is not None else ""


def parse(data: bytes) -> Feed:
    """Parse an Atom feed. Raises FeedError on anything that isn't one (for example an HTML page)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FeedError(f"not a valid feed: {e}") from None
    if root.tag != f"{{{NS['atom']}}}feed":
        raise FeedError("not an Atom feed")
    total = _find(root, "opensearch:totalResults")
    feed = Feed(title=_find(root, "atom:title"), total=int(total) if total.isdigit() else None)
    for el in root.findall("atom:entry", NS):
        entry = Entry(
            id=_find(el, "atom:id"),
            title=_find(el, "atom:title"),
            authors=[_find(a, "atom:name") for a in el.findall("atom:author", NS) if _find(a, "atom:name")],
            content=_find(el, "atom:content"),
            published=_find(el, "atom:published"),
            updated=_find(el, "atom:updated"),
            issued=_find(el, "dc:issued") or _find(el, "dcterms:issued"),
            language=_find(el, "dc:language") or _find(el, "dcterms:language"),
            publisher=_find(el, "dc:publisher") or _find(el, "dcterms:publisher"),
            categories=[c.get("term", "") for c in el.findall("atom:category", NS) if c.get("term")],
        )
        for link in el.findall("atom:link", NS):
            entry.links.append(
                Link(
                    href=link.get("href", ""),
                    rel=link.get("rel", "alternate"),
                    type=link.get("type", ""),
                    title=link.get("title", ""),
                    length=link.get("length", ""),
                )
            )
        for child in el:
            if child.tag.startswith(f"{{{NS['arxiv']}}}"):
                key = child.tag.split("}", 1)[1]
                entry.extra[key] = child.get("term") or text(child.text)
        feed.entries.append(entry)
    return feed
