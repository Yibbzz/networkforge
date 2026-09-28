"""
The OSM access hierarchy on top of OSMnx's filters: the most specific
access tag for a mode decides.
"""

import pytest

from networkforge.modes import effective_access, usable_modes

ROADS = ["drive", "drive_service", "walk", "bike"]


@pytest.mark.parametrize("tags, expected", [
    # Closed to everyone / to some modes
    ({"highway": "primary", "access": "no"}, []),
    ({"highway": "primary", "access": "agricultural"}, []),
    ({"highway": "primary", "vehicle": "no"}, ["walk"]),
    ({"highway": "primary", "motor_vehicle": "private"}, ["walk", "bike"]),
    ({"highway": "primary", "bicycle": "use_sidepath"}, ["drive", "drive_service", "walk"]),
    ({"highway": "primary", "foot": "no"}, ["drive", "drive_service", "bike"]),
    # The most specific tag wins
    ({"highway": "primary", "access": "no", "bicycle": "yes"}, ["bike"]),
    ({"highway": "primary", "access": "no", "foot": "designated"}, ["walk"]),
    ({"highway": "primary", "motor_vehicle": "no", "motorcar": "yes"},
     ["drive", "drive_service", "walk", "bike"]),
    ({"highway": "track", "access": "agricultural", "foot": "yes"}, ["walk"]),
    # Restricted-but-open values stay open
    ({"highway": "residential", "motor_vehicle": "destination"}, ROADS),
    ({"highway": "residential", "access": "customers"}, ROADS),
    ({"highway": "residential", "bicycle": "dismount"}, ROADS),
    # Busways are for buses unless a mode is explicitly allowed
    ({"highway": "busway"}, []),
    ({"highway": "busway", "access": "yes"}, []),
    ({"highway": "busway", "bicycle": "designated"}, ["bike"]),
    # Explicit tags open ways OSMnx's type filter excludes
    ({"highway": "footway", "bicycle": "designated"}, ["walk", "bike"]),
    ({"highway": "footway", "bicycle": "yes", "access": "no"}, ["bike"]),
    ({"highway": "footway", "access": "yes"}, ["walk"]),
    ({"highway": "cycleway", "foot": "yes"}, ["walk", "bike"]),
    # ...but never motorways for walking, or footways for cars
    ({"highway": "motorway", "foot": "yes"}, ["drive", "drive_service"]),
    ({"highway": "footway", "motor_vehicle": "yes"}, ["walk"]),
])
def test_access_hierarchy(tags, expected):
    assert usable_modes(tags) == expected


@pytest.mark.parametrize("tags, mode, expected", [
    ({"access": "no", "bicycle": "yes"}, "bike", ("bicycle", "yes")),
    ({"access": "no", "bicycle": "yes"}, "drive", ("access", "no")),
    ({"access": "no", "vehicle": "yes", "motorcar": "no"}, "drive", ("motorcar", "no")),
    ({"highway": "primary"}, "walk", None),
    ({"access": "no"}, "all", None),
])
def test_effective_access(tags, mode, expected):
    assert effective_access(tags, mode) == expected
