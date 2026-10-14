"""Book sources. Each module has a ``Source`` class with ``from_env``, ``search``, ``download`` and ``quota``."""

from . import annas, zlib

SOURCES = {"annas": annas, "zlib": zlib}
