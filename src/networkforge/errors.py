"""
Exceptions raised by NetworkForge.

Catch NetworkForgeError to handle anything the pipeline rejects. Every
error has a plain-language message; `guide` names the section of
docs/tagging-guide.md that explains the fix, where there is one.

    NetworkForgeError
    ├── InputError (also a ValueError)       bad arguments or input data
    │   ├── InvalidTagsError                 custom feature tags break OSM rules
    │   └── NoIntersectionError              custom data doesn't touch the OSM network
    ├── OSMDownloadError                     the OSM download failed
    └── NetworkIntegrityError                the built network breaks a structural rule
"""

DOCS = "docs"
GUIDE = f"{DOCS}/tagging-guide.md"


class NetworkForgeError(Exception):
    """
    Base class for every error NetworkForge raises on purpose.

    `guide` is a section of the tagging guide ("presets") or another
    doc in docs/ ("osm-data.md#getting-an-extract").
    """

    def __init__(self, message: str, guide: str | None = None):
        super().__init__(message)
        self.guide = guide

    def __str__(self) -> str:
        message = super().__str__()
        if self.guide:
            where = f"{DOCS}/{self.guide}" if ".md" in self.guide else f"{GUIDE}#{self.guide}"
            message += f"\n(see {where})"
        return message


class InputError(NetworkForgeError, ValueError):
    """An argument or the input data can't be used."""


class InvalidTagsError(InputError):
    """One or more custom features have tags that break OSM rules."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            f"{len(problems)} custom tag problem(s):\n  - " + "\n  - ".join(problems),
            guide="fixing-tag-errors",
        )


class NoIntersectionError(InputError):
    """The custom data doesn't touch the existing OSM network."""


class OSMDownloadError(NetworkForgeError):
    """The OSM network couldn't be downloaded (network, Overpass or empty area)."""


class NetworkIntegrityError(NetworkForgeError):
    """The built network breaks a structural rule (see validation.py)."""
