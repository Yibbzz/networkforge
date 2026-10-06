"""
Changing existing streets, proved in Valhalla: a custom feature with an
OSM way id changes that street's tags (src/networkforge/edits.py).

Trips run along Row 1 Street, from node 6 to node 9 (300 m), through
the block between nodes 7 and 8 that the edits change.
"""

import pytest

from tests.integration.grid import NO_LEFT_TURN, SLIP_ROAD
from tests.integration.test_edits import ROW_1, SLIP, way_id
from tests.valhalla.conftest import at, turn_metres, xy

BLOCK = [xy(1, 1), xy(2, 1)]
WEST, EAST = at(0, 1), at(3, 1)
VEHICLES = ("auto", "bus", "truck")
EVERYONE = (*VEHICLES, "bicycle", "pedestrian")


def metres(built, which, start, end, costing, **options):
    route = built.route(which, start, end, costing, shortest=True, **options)
    return None if route is None else round(route.length_m)


def changed_way(built) -> int:
    (way,) = [w for w, (_, tags) in built.after.ways.items()
              if tags.get("nf:modified") == "yes"]
    return way


def test_street_made_one_way(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "oneway": "yes"})])

    for costing in EVERYONE:
        assert metres(built, "before", WEST, EAST, costing) == 300
        assert metres(built, "before", EAST, WEST, costing) == 300
        assert metres(built, "after", WEST, EAST, costing) == 300
    for costing in (*VEHICLES, "bicycle"):
        assert metres(built, "after", EAST, WEST, costing) == 500  # round the block
    assert metres(built, "after", EAST, WEST, "pedestrian") == 300


def test_one_way_reversed_by_drawing_the_feature_the_other_way(scenario):
    built = scenario([(BLOCK[::-1], {"osm_id": ROW_1, "oneway": "yes"})])
    assert metres(built, "after", EAST, WEST, "auto") == 300
    assert metres(built, "after", WEST, EAST, "auto") == 500


def test_contraflow_cycling_on_the_new_one_way(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "oneway": "yes", "oneway:bicycle": "no"})])
    assert metres(built, "after", EAST, WEST, "auto") == 500
    assert metres(built, "after", EAST, WEST, "bicycle") == 300


def test_existing_one_way_made_two_way(scenario):
    """The slip road onto the motorway (5 -> 100) only runs one way in OSM."""
    slip = [xy(4, 0), xy(5, 0)]
    start, end = at(5, 0), at(4, 0)
    built = scenario([(slip, {"osm_id": SLIP, "oneway": "no"})])

    assert built.before.node(SLIP_ROAD[1]) == pytest.approx(start)
    assert metres(built, "before", start, end, "auto") is None
    assert metres(built, "after", start, end, "auto") == 100


def test_street_closed(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "access": "no"})])
    for costing in EVERYONE:
        assert metres(built, "after", WEST, EAST, costing) == 500, costing


def test_street_closed_to_motor_traffic_only(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "motor_vehicle": "no"})])
    for costing in VEHICLES:
        assert metres(built, "after", WEST, EAST, costing) == 500
    for costing in ("bicycle", "pedestrian"):
        assert metres(built, "after", WEST, EAST, costing) == 300


def test_street_pedestrianised(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "highway": "pedestrian"})])
    assert metres(built, "after", WEST, EAST, "pedestrian") == 300
    for costing in (*VEHICLES, "bicycle"):
        assert metres(built, "after", WEST, EAST, costing) == 500


def test_bus_gate_opened_to_cars(scenario):
    """Row 4 Street between nodes 21 and 22 is motor_vehicle=no in OSM."""
    gate = way_id(21, 22)
    built = scenario([([xy(0, 4), xy(1, 4)], {"osm_id": gate, "motor_vehicle": "yes"})])
    assert metres(built, "before", at(0, 4), at(1, 4), "auto") > 250
    assert metres(built, "after", at(0, 4), at(1, 4), "auto") == 100


def test_bus_gate_removed_by_deleting_its_tag(scenario):
    """remove_tags=motor_vehicle: the street is an ordinary residential street again."""
    gate = way_id(21, 22)
    built = scenario([([xy(0, 4), xy(1, 4)], {"osm_id": gate, "remove_tags": "motor_vehicle"})])
    assert "motor_vehicle" not in built.after.ways[changed_way(built)][1]
    assert metres(built, "after", at(0, 4), at(1, 4), "auto") == 100


def test_deleting_maxspeed_falls_back_to_valhallas_default(scenario):
    whole_street = [xy(0, 1), xy(4, 1)]
    built = scenario([(whole_street, {"osm_id": ROW_1, "remove_tags": "maxspeed"})])
    for edge in built.after.edges(changed_way(built)):
        assert edge["edge_info"].get("speed_limit", 0) in (0, 255)  # none tagged
        assert edge["edge"]["speeds"]["type"] == "classified"


def test_one_way_slip_road_made_two_way_by_deleting_oneway(scenario):
    slip = [xy(4, 0), xy(5, 0)]
    built = scenario([(slip, {"osm_id": SLIP, "remove_tags": "oneway"})])
    assert metres(built, "after", at(5, 0), at(4, 0), "auto") == 100


def test_lower_speed_limit_takes_longer_over_the_same_distance(scenario):
    whole_street = [xy(0, 1), xy(4, 1)]
    built = scenario([(whole_street, {"osm_id": ROW_1, "maxspeed": "10 mph"})])
    before = built.before.route(WEST, EAST, "auto", shortest=True)
    after = built.after.route(WEST, EAST, "auto", shortest=True)

    assert after.length_m == before.length_m
    assert after.time_s == pytest.approx(before.time_s * 3, rel=0.15)  # 30 mph -> 10 mph
    for edge in built.after.edges(changed_way(built)):
        assert edge["edge_info"]["speed_limit"] == 16


def test_height_limit_added_to_an_existing_street(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "maxheight": "3"})])
    assert metres(built, "after", WEST, EAST, "auto") == 300
    assert metres(built, "after", WEST, EAST, "truck") == 500
    assert metres(built, "after", WEST, EAST, "truck", height=2.5) == 300


def test_unpaved_surface_on_an_existing_street(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "surface": "gravel"})])
    assert metres(built, "after", WEST, EAST, "auto") == 300
    assert metres(built, "after", WEST, EAST, "auto", exclude_unpaved=True) == 500


def test_rest_of_the_street_and_the_network_are_unchanged(scenario):
    """One block changes; every trip that doesn't need it westbound stays the same."""
    built = scenario([(BLOCK, {"osm_id": ROW_1, "oneway": "yes"})])
    points = [built.before.node(n) for n in range(1, 26)]

    def distances(router, costing):
        return [[cell and cell[0] for cell in row]
                for row in router.matrix(points, costing, shortest=True)]

    assert distances(built.after, "pedestrian") == distances(built.before, "pedestrian")
    before = built.before.matrix(points, "auto", shortest=True)
    after = built.after.matrix(points, "auto", shortest=True)
    longer = sum(a[0] > b[0] for row_a, row_b in zip(after, before, strict=True)
                 for a, b in zip(row_a, row_b, strict=True) if a and b)
    shorter = sum(a[0] < b[0] for row_a, row_b in zip(after, before, strict=True)
                  for a, b in zip(row_a, row_b, strict=True) if a and b)
    assert longer > 0 and shorter == 0


def test_names_and_other_tags_stay_on_the_changed_stretch(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "oneway": "yes"})])
    route = built.after.route(WEST, EAST, "auto", shortest=True)

    assert route.names == ("Row 1 Street",)
    assert route.way_ids == (ROW_1, changed_way(built), route.way_ids[-1])
    assert built.after.ways[changed_way(built)][1]["maxspeed"] == "30 mph"


@pytest.mark.parametrize("member", ["from", "to"])
def test_turn_restriction_still_applies_when_its_way_is_changed(scenario, member):
    """
    The restriction's "from" (or "to") way gets a new speed limit, and
    so is written under a new way id: the restriction must follow it.
    """
    (from_u, from_v), _, (to_u, to_v) = NO_LEFT_TURN
    feature = {
        "from": ([xy(1, 3), xy(2, 3)], {"osm_id": way_id(from_u, from_v), "maxspeed": "20 mph"}),
        "to": ([xy(2, 3), xy(2, 4)], {"osm_id": way_id(to_u, to_v), "maxspeed": "20 mph"}),
    }[member]
    built = scenario([feature])

    assert built.after.ways[changed_way(built)][1]["maxspeed"] == "20 mph"
    assert turn_metres(built.after, "auto") == pytest.approx(300, abs=2)
    assert turn_metres(built.after, "pedestrian") == pytest.approx(100, abs=2)


def test_new_line_joins_a_changed_street(scenario):
    """A new road meets the block that was made one-way, halfway along it."""
    spur = [xy(1.5, 1), xy(1.5, 1.5)]
    built = scenario([(BLOCK, {"osm_id": ROW_1, "oneway": "yes"}),
                      (spur, {"highway": "residential"})])
    (road,) = built.custom_ways
    top = at(1.5, 1.5)

    # Out of the new road you may only turn east; into it only from the west.
    assert metres(built, "after", top, at(2, 1), "auto") == 100
    assert metres(built, "after", top, at(1, 1), "auto") > 150
    assert built.after.route(at(1, 1), top, "auto", shortest=True).uses(road)
    assert metres(built, "after", at(1, 1), top, "auto") == 100


# ---------------------------------------------------------------------
# Removing existing streets: remove=yes
# ---------------------------------------------------------------------

def test_street_removed(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "remove": "yes"})])

    for costing in EVERYONE:
        assert metres(built, "before", WEST, EAST, costing) == 300
        assert metres(built, "after", WEST, EAST, costing) == 500, costing
    # Not just closed: the stretch isn't in the file, so nothing snaps to it.
    assert not any(refs == [7, 8] for refs, _ in built.after.ways.values())
    assert sorted(refs for refs, tags in built.after.ways.values()
                  if tags.get("name") == "Row 1 Street") == [[6, 7], [8, 9, 10]]


def test_removed_street_replaced_by_a_new_line(scenario):
    """Take out a block and draw a footpath where it was: walkers only."""
    built = scenario([(BLOCK, {"osm_id": ROW_1, "remove": "yes"}),
                      (BLOCK, {"highway": "footway"})])
    (path,) = built.custom_ways

    on_foot = built.after.route(WEST, EAST, "pedestrian", shortest=True)
    assert on_foot.uses(path) and round(on_foot.length_m) == 300
    assert metres(built, "after", WEST, EAST, "auto") == 500


def test_rest_of_the_network_is_untouched_by_a_removal(scenario):
    built = scenario([(BLOCK, {"osm_id": ROW_1, "remove": "yes"})])
    unchanged = {way: value for way, value in built.before.ways.items() if way != ROW_1}
    assert {way: built.after.ways.get(way) for way in unchanged} == unchanged
    assert turn_metres(built.after, "auto") == pytest.approx(300, abs=2)
