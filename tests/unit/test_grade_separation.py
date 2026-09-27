"""Unit tests for the at-grade crossing rule."""

import pytest

from networkforge.topology import crosses_at_grade, is_grade_separated


@pytest.mark.parametrize("tags, expected", [
    ({"highway": "residential"}, False),
    ({"highway": "trunk"}, False),
    ({"highway": "motorway"}, True),
    ({"highway": "motorway_link"}, True),
    ({"highway": "primary", "bridge": "yes"}, True),
    ({"highway": "primary", "bridge": "viaduct"}, True),
    ({"highway": "primary", "bridge": "no"}, False),
    ({"highway": "footway", "tunnel": "yes"}, True),
    ({"highway": "primary", "bridge": float("nan")}, False),
])
def test_is_grade_separated(tags, expected):
    assert is_grade_separated(tags) is expected


@pytest.mark.parametrize("a, b, expected", [
    ({"highway": "primary"}, {"highway": "footway"}, True),
    ({"highway": "primary"}, {"highway": "motorway"}, False),
    ({"highway": "primary", "bridge": "yes"}, {"highway": "residential"}, False),
    ({"highway": "primary", "layer": "1"}, {"highway": "residential"}, False),
    ({"highway": "primary", "layer": "1"}, {"highway": "residential", "layer": "1"}, True),
    ({"highway": "primary", "layer": "0"}, {"highway": "residential"}, True),
    ({"highway": "primary", "layer": "junk"}, {"highway": "residential"}, True),
])
def test_crosses_at_grade(a, b, expected):
    assert crosses_at_grade(a, b) is expected
    assert crosses_at_grade(b, a) is expected
