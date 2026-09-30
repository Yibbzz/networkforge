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


def feature_id(value):
    """A feature id (custom data index value) as a plain JSON-friendly value."""
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    return value if isinstance(value, int | float | str) or value is None else str(value)


class NetworkForgeError(Exception):
    """
    Base class for every error NetworkForge raises on purpose.

    `guide` is a section of the tagging guide ("presets") or another
    doc in docs/ ("osm-data.md#getting-an-extract").

    `issues` pinpoints problems per custom feature, as a list of
    {"feature": id, "message": text} (feature None for problems that
    aren't about one feature). Feature ids are the custom data's index
    values - set the index to an id column to get your own ids.
    """

    def __init__(self, message: str, guide: str | None = None, issues: list | None = None):
        super().__init__(message)
        self.guide = guide
        self.issues = [{"feature": feature_id(i["feature"]), "message": i["message"]}
                       for i in issues or []]

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

    def __init__(self, issues: list[tuple]):
        """issues: (feature id or None, message) pairs."""
        self.problems = [
            message if feature is None else f"feature {feature_id(feature)}: {message}"
            for feature, message in issues
        ]
        super().__init__(
            f"{len(self.problems)} custom tag problem(s):\n  - " + "\n  - ".join(self.problems),
            guide="fixing-tag-errors",
            issues=[{"feature": feature, "message": message} for feature, message in issues],
        )


class NoIntersectionError(InputError):
    """The custom data doesn't touch the existing OSM network."""


class OSMDownloadError(NetworkForgeError):
    """The OSM network couldn't be downloaded (network, Overpass or empty area)."""


class NetworkIntegrityError(NetworkForgeError):
    """The built network breaks a structural rule (see validation.py)."""
