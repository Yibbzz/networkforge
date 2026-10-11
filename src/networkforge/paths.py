"""
File names osmium can open on Windows.

osmium (the library that reads and writes .osm.pbf) opens files through
an old Windows interface that doesn't take Unicode names: a file in
"C:\\Users\\Zoë\\Documents" is "not found". Python itself has no such
trouble, so the two helpers here let Python do the part with the
awkward letters:

- the folder: Python makes it the working folder, and osmium is given
  the bare file name;
- the file name, if that has such letters too: osmium reads a second,
  plainly named entry for the same file (a hard link, no copy), or
  writes to a plainly named file that is renamed when it is finished.

Everywhere else, and for plain ASCII paths on Windows, the path is
passed on untouched.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import InputError

# Longest first: "x.osm.pbf" ends in ".pbf" too.
OSM_ENDINGS = (".osm.pbf", ".osm.bz2", ".osm.gz", ".pbf", ".osm")

# Whether osmium needs this help (tests switch it on to try it anywhere).
_NARROW_NAMES_ONLY = os.name == "nt"


def _needs_help(path: Path) -> bool:
    return _NARROW_NAMES_ONLY and not str(path).isascii() and path.parent.is_dir()


def _plain_name(name: str) -> str:
    """A name of plain letters that osmium reads as the same format as `name`."""
    ending = next((e for e in OSM_ENDINGS if name.lower().endswith(e)), "")
    return f"networkforge-{uuid.uuid4().hex}{ending}"


@contextmanager
def _working_in(folder: Path) -> Iterator[None]:
    before = os.getcwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(before)


def _remove(name: str) -> None:
    try:
        os.unlink(name)
    except OSError:  # never there, or Windows still has it open
        pass


@contextmanager
def readable(path: str | Path) -> Iterator[str]:
    """
    Give the name to hand to osmium to read `path`. Keep reading inside
    the `with` block: the name is only good there.
    """
    path = Path(path)
    if not _needs_help(path):
        yield str(path)
        return

    path = path.absolute()
    with _working_in(path.parent):
        if path.name.isascii():
            yield path.name
            return
        link = _plain_name(path.name)
        try:
            os.link(path.name, link)
        except OSError as exc:
            raise InputError(
                f"Can't open {path.name}: on Windows, give the file a name without "
                "accented or other special letters and try again."
            ) from exc
        try:
            yield link
        finally:
            _remove(link)


@contextmanager
def writable(path: str | Path) -> Iterator[str]:
    """
    Give the name to hand to osmium to write `path`. The file is in
    place under its real name when the `with` block ends without error.
    """
    path = Path(path)
    if not _needs_help(path):
        yield str(path)
        return

    path = path.absolute()
    with _working_in(path.parent):
        if path.name.isascii():
            yield path.name
            return
        unfinished = _plain_name(path.name)
        try:
            yield unfinished
            os.replace(unfinished, path.name)
        finally:
            _remove(unfinished)
