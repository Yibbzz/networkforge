"""
Wheelchair routing, proved in Valhalla: its walking costing with
`type=wheelchair`. One new path across a block of the grid (SHORTCUT in
test_esri_network_dataset.py): a trip takes it (341 m) or goes round by
the streets (400 m).
"""

import pytest

from tests.integration.test_edits import way_id
from tests.valhalla.conftest import at, xy
from tests.valhalla.test_esri_network_dataset import (
    FROM,
    SHORTCUT,
    TO,
    VIA_SHORTCUT,
    VIA_STREETS,
)

WHEELCHAIR = {"type": "wheelchair"}


def trip(scenario, tags, **options):
    """(does the walking route take the new path?, the route)"""
    built = scenario([(SHORTCUT, tags)], strict=False)
    route = built.after.route(FROM, TO, "pedestrian", shortest=True, **options)
    used = route.uses(built.custom_ways[0])
    assert route.length_m == pytest.approx(VIA_SHORTCUT if used else VIA_STREETS, abs=2)
    return used, route


@pytest.mark.parametrize("tags, wheelchair_can_use_it", [
    ({"highway": "footway"}, True),
    ({"highway": "footway", "wheelchair": "yes"}, True),
    ({"highway": "residential", "sidewalk": "no"}, True),
    ({"highway": "steps"}, False),
    ({"highway": "footway", "wheelchair": "no"}, False),
    ({"highway": "footway", "surface": "gravel"}, False),
])
def test_what_a_wheelchair_can_use(scenario, tags, wheelchair_can_use_it):
    on_foot, _ = trip(scenario, tags)
    in_a_wheelchair, _ = trip(scenario, tags, **WHEELCHAIR)

    assert on_foot                                  # nothing here stops a walker
    assert in_a_wheelchair is wheelchair_can_use_it


def test_a_wheelchair_is_slower_than_a_walker(scenario):
    _, on_foot = trip(scenario, {"highway": "footway"})
    _, in_a_wheelchair = trip(scenario, {"highway": "footway"}, **WHEELCHAIR)

    assert in_a_wheelchair.time_s > on_foot.time_s * 1.2


def test_a_steep_path_is_not_ruled_out(scenario):
    """Valhalla 3.9 doesn't read `incline`; it would need elevation data, which a
    network built here has none of. If this fails, it has started to."""
    in_a_wheelchair, _ = trip(scenario, {"highway": "footway", "incline": "15%"}, **WHEELCHAIR)

    assert in_a_wheelchair


def test_an_existing_path_turned_into_steps_stops_wheelchairs_only(scenario):
    """The grid's footway (nodes 8-13), changed to steps by its OSM id."""
    footway = [xy(2, 1), xy(2, 2)]
    below, above = at(2, 0.5), at(2, 2.5)
    built = scenario([(footway, {"osm_id": way_id(8, 13), "highway": "steps"})])

    def metres(which, **options):
        return round(built.route(which, below, above, "pedestrian", shortest=True,
                                 **options).length_m)

    assert metres("before") == metres("before", **WHEELCHAIR) == 200
    assert metres("after") == 200
    assert metres("after", **WHEELCHAIR) == 400     # round the block


# What Valhalla 3.9 does not read for wheelchairs. NetworkForge writes
# these tags (checked below), so another router - or a later Valhalla -
# can use them; if one of these tests fails, Valhalla has started to.

@pytest.mark.parametrize("ramp", [
    {"ramp:wheelchair": "yes"},
    {"ramp": "yes"},
    {"wheelchair": "yes"},
])
def test_steps_with_a_ramp_are_still_steps_to_valhalla(scenario, ramp):
    built = scenario([(SHORTCUT, {"highway": "steps", **ramp})])
    (way,) = built.custom_ways
    assert built.after.ways[way][1].items() >= ramp.items()   # in the PBF

    in_a_wheelchair, _ = trip(scenario, {"highway": "steps", **ramp}, **WHEELCHAIR)
    assert not in_a_wheelchair


@pytest.mark.parametrize("kerb", [
    {"barrier": "kerb"},
    {"barrier": "kerb", "kerb": "raised"},
    {"barrier": "kerb", "wheelchair": "no"},
    {"kerb": "raised"},
])
def test_valhalla_does_not_stop_a_wheelchair_at_a_kerb(scenario, kerb):
    """A kerb (a point) in the middle of the new path."""
    built = scenario([(SHORTCUT, {"highway": "footway"}), ((xy(1.5, 1.5),), kerb)])
    (way,) = built.custom_ways
    refs = built.after.ways[way][0]
    assert any(built.after.node_tags(ref) == kerb for ref in refs)   # in the PBF

    route = built.after.route(FROM, TO, "pedestrian", shortest=True, **WHEELCHAIR)
    assert route.uses(way)
