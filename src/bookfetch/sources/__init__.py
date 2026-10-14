"""Book sources. Each module has a ``Source`` class with ``from_account``, ``search``, ``download`` and ``quota``.

annas and zlib need accounts; the others are open sources that work without one
(see ``base.OpenSource``).
"""

from . import annas, gutenberg, standardebooks, zlib

SOURCES = {
    "annas": annas,
    "zlib": zlib,
    "gutenberg": gutenberg,
    "standardebooks": standardebooks,
}
