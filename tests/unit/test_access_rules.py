"""
The OSM access hierarchy on top of OSMnx's filters: the most specific
access tag for a mode decides.
"""

import pytest

from networkforge.modes import effective_access, open_to_other_vehicles, usable_modes

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
    # Pedestrian streets: cycling only where a tag allows it
    ({"highway": "pedestrian"}, ["walk"]),
    ({"highway": "pedestrian", "bicycle": "yes"}, ["walk", "bike"]),
    ({"highway": "pedestrian", "access": "yes"}, ["walk"]),
    # motorroad=yes: motorway rules on any road
    ({"highway": "trunk", "motorroad": "yes"}, ["drive", "drive_service"]),
    ({"highway": "trunk", "motorroad": "yes", "bicycle": "yes"},
     ["drive", "drive_service", "bike"]),
    ({"highway": "trunk", "motorroad": "no"}, ROADS),
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


@pytest.mark.parametrize("tags, expected", [
    ({"highway": "busway"}, True),
    ({"highway": "bus_guideway"}, True),
    ({"highway": "residential", "access": "no", "bus": "yes"}, True),
    ({"highway": "residential", "motor_vehicle": "no", "psv": "designated"}, True),
    ({"highway": "residential", "access": "no", "emergency": "yes"}, True),
    ({"highway": "residential", "access": "no"}, False),
    ({"highway": "residential", "access": "no", "bus": "no"}, False),
    ({"highway": "residential", "access": "private"}, False),
])
def test_open_to_other_vehicles(tags, expected):
    assert open_to_other_vehicles(tags) is expected
