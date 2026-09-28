"""NetworkForge: integrate custom infrastructure into OpenStreetMap networks."""

import logging
from importlib.metadata import version

from .errors import (
    InputError,
    InvalidTagsError,
    NetworkForgeError,
    NetworkIntegrityError,
    NoIntersectionError,
    OSMDownloadError,
)
from .export import write_gpkg, write_osm, write_osm_xml
from .network import build_network
from .presets import PRESETS

__version__ = version("networkforge")  # single source: pyproject.toml

__all__ = [
    "PRESETS",
    "InputError",
    "InvalidTagsError",
    "NetworkForgeError",
    "NetworkIntegrityError",
    "NoIntersectionError",
    "OSMDownloadError",
    "build_network",
    "write_gpkg",
    "write_osm",
    "write_osm_xml",
]

# Libraries shouldn't print or configure logging; applications opt in
# with logging.basicConfig(level=logging.INFO).
logging.getLogger(__name__).addHandler(logging.NullHandler())
