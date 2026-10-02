"""
How custom lines join the network, proved by routing through the joins
in Valhalla: every way a line can meet a street, another custom line or
itself, and every way it must not.

Positions are grid coordinates (column, row), 100 m apart: xy() for
drawing, at() for routing. See tests/integration/grid.py for the map.
"""

import logging

import pytest

from tests.integration.grid import NO_LEFT_TURN
from tests.valhalla.conftest import at, xy

ROAD = {"highway": "residential"}


def shortest(built, start, end, costing="auto", which="after"):
    return built.route(which, start, end, costing, shortest=True)


def junctions(built) -> set[int]:
    """Nodes the custom ways share with existing OSM ways."""
    custom = {ref for way in built.custom_ways for ref in built.after.ways[way][0]}
    return {
        ref for way, (refs, _) in built.after.ways.items() if way not in built.custom_ways
        for ref in refs if ref in custom
    }


def new_nodes(built) -> set[int]:
    return set(built.after._nodes_by_id()) - set(built.before._nodes_by_id())


# ---------------------------------------------------------------------
# Joining existing streets
# ---------------------------------------------------------------------

def test_line_crossing_streets_mid_block_joins_each_one(scenario):
    """A north-south road half a block east of Column 0 crosses the five row streets."""
    built = scenario([([xy(0.5, -0.3), xy(0.5, 4.3)], ROAD)])
    (way,) = built.custom_ways

    assert len(junctions(built)) == 5
    assert junctions(built) <= new_nodes(built)

    along = shortest(built, at(0.5, 0), at(0.5, 4), "pedestrian")
    assert along.way_ids == (way,) and along.length_m == pytest.approx(400, abs=2)

    # ...and you can turn off it: from halfway between two rows, onto Row 1 Street.
    turning = shortest(built, at(0.5, 0.5), at(2, 1))
    assert turning.way_ids[0] == way and turning.length_m == pytest.approx(50 + 150, abs=2)


def test_crossed_street_still_runs_straight_through(scenario):
    """The new junction must not cut, bend or lengthen the street it is on."""
    built = scenario([([xy(0.5, -0.3), xy(0.5, 4.3)], ROAD)])
    for costing in ("auto", "bicycle", "pedestrian"):
        before = shortest(built, at(0, 1), at(4, 1), costing, "before")
        after = shortest(built, at(0, 1), at(4, 1), costing)
        assert after.way_ids == before.way_ids
        assert after.length_m == pytest.approx(before.length_m, abs=1)


@pytest.mark.parametrize("start", [
    xy(1, 1),            # exactly on node 7
    xy(1.004, 1.003),    # half a metre from it
])
def test_end_on_or_near_a_junction_joins_that_junction(scenario, start):
    built = scenario([([start, xy(2, 2)], ROAD)])
    assert built.after.ways[built.custom_ways[0]][0] == [7, 13]
    assert not new_nodes(built)
    assert shortest(built, at(0, 1), at(3, 2)).length_m == pytest.approx(341, abs=2)


def test_end_just_short_of_a_street_is_pulled_onto_it(scenario):
    """Both ends half a metre short of the streets, mid-block."""
    built = scenario([([xy(1.5, 1.005), xy(1.5, 1.995)], ROAD)])
    (way,) = built.custom_ways

    assert len(junctions(built)) == 2
    route = shortest(built, at(1.5, 1), at(1.5, 2), "pedestrian")
    assert route.way_ids == (way,) and route.length_m == pytest.approx(100, abs=2)


def test_end_outside_the_snap_distance_stays_a_dead_end(scenario, caplog):
    """3 m short of Row 1 Street: joined at the top only."""
    built = scenario([([xy(1.5, 1.03), xy(1.5, 2)], ROAD)])

    assert len(junctions(built)) == 1
    assert shortest(built, at(1.5, 1), at(1.5, 2), "pedestrian").length_m > 150


def test_snap_tolerance_is_adjustable(scenario):
    line = [xy(1.03, 1.03), xy(2, 2)]  # starts about 4 m from node 7
    assert scenario([(line, ROAD)]).after.ways[21][0][0] != 7
    assert scenario([(line, ROAD)], snap_tolerance=5).after.ways[21][0] == [7, 13]


def test_middle_vertex_on_a_junction_joins_it(scenario):
    built = scenario([([xy(0.5, 1), xy(1, 2), xy(1.5, 3)], ROAD)])
    (way,) = built.custom_ways

    assert 12 in built.after.ways[way][0]
    onto_the_street = shortest(built, at(0.5, 1), at(2, 2))  # leaves the line at node 12
    assert onto_the_street.uses(way)
    assert onto_the_street.length_m == pytest.approx(111.8 + 100, abs=2)


def test_each_part_of_a_multi_line_joins(scenario):
    built = scenario([([[xy(1, 1), xy(2, 2)], [xy(3, 3), xy(4, 4)]], ROAD)])
    first, second = built.custom_ways

    assert shortest(built, at(1, 1), at(2, 2)).way_ids == (first,)
    assert shortest(built, at(3, 3), at(4, 4)).way_ids == (second,)


def test_closed_loop_joins_where_it_touches(scenario):
    ring = [xy(3, 3), xy(3.5, 3.2), xy(3.8, 3.5), xy(3.2, 3.8), xy(3, 3)]
    built = scenario([(ring, ROAD)])
    (way,) = built.custom_ways
    refs = built.after.ways[way][0]

    assert refs[0] == refs[-1] == 19 and junctions(built) == {19}
    assert shortest(built, at(3, 3), at(3.8, 3.5), "pedestrian").way_ids == (way,)


def test_very_short_line_and_repeated_vertices(scenario):
    stub = scenario([([xy(1.5, 1), xy(1.55, 1.05)], ROAD)])  # 7 m spur off Row 1 Street
    assert len(junctions(stub)) == 1
    assert shortest(stub, at(1, 1), at(2, 1)).length_m == pytest.approx(100, abs=1)

    repeated = scenario([([xy(1, 1), xy(1, 1), xy(1.5, 1.5), xy(1.5, 1.5), xy(2, 2)], ROAD)])
    assert len(repeated.after.ways[repeated.custom_ways[0]][0]) == 3
    assert shortest(repeated, at(1, 1), at(2, 2)).length_m == pytest.approx(141, abs=2)


def test_line_drawn_along_an_existing_street(scenario):
    """
    Drawn on top of Row 0 Street: the line is added beside it (changing
    an existing street isn't supported), joined at the shared junctions.
    """
    built = scenario([([xy(0, 0), xy(1, 0), xy(2, 0)], {"highway": "primary"})])
    (way,) = built.custom_ways

    assert built.after.ways[way][0] == [1, 2, 3]
    assert built.after.ways[1][0] == [1, 2, 3, 4, 5]
    assert shortest(built, at(0, 0), at(2, 0)).length_m == pytest.approx(200, abs=2)


# ---------------------------------------------------------------------
# Custom lines meeting each other, and themselves
# ---------------------------------------------------------------------

def test_two_custom_lines_crossing_inside_a_block_join(scenario):
    built = scenario([([xy(3, 3), xy(4, 4)], ROAD), ([xy(3, 4), xy(4, 3)], ROAD)])
    rising, falling = built.custom_ways
    (crossing,) = set(built.after.ways[rising][0]) & set(built.after.ways[falling][0])

    assert crossing in new_nodes(built)
    route = shortest(built, at(3.2, 3.2), at(3.8, 3.2), "pedestrian")  # around the crossing
    assert route.way_ids == (rising, falling)


def test_custom_line_ending_on_another_custom_line(scenario):
    built = scenario([([xy(3, 3), xy(4, 4)], ROAD), ([xy(3.5, 3.5), xy(4, 3)], ROAD)])
    main, branch = built.custom_ways

    assert built.after.ways[branch][0][0] in built.after.ways[main][0]
    assert shortest(built, at(3.2, 3.2), at(3.8, 3.2), "pedestrian").way_ids == (main, branch)


def test_custom_line_ending_just_short_of_another_joins_it(scenario):
    built = scenario([([xy(3, 3), xy(4, 4)], ROAD), ([xy(3.504, 3.5), xy(4, 3)], ROAD)])
    main, branch = built.custom_ways
    assert built.after.ways[branch][0][0] in built.after.ways[main][0]


def test_three_custom_lines_meeting_at_one_point(scenario):
    centre = xy(3.5, 3.5)
    built = scenario([([xy(3, 3), centre], ROAD), ([xy(4, 3), centre], ROAD),
                      ([centre, xy(3.5, 4)], ROAD)])
    (shared,) = set.intersection(*(set(built.after.ways[w][0]) for w in built.custom_ways))

    assert shared in new_nodes(built)
    assert shortest(built, at(3, 3), at(3.5, 4)).length_m == pytest.approx(70.7 + 50, abs=2)


def test_line_crossing_itself_gets_a_junction_there(scenario):
    """(3,3) -> (4,4) -> (4,3.5) -> back west across its own first stretch."""
    loop = [xy(3, 3), xy(4, 4), xy(4, 3.5), xy(3, 3.5)]
    built = scenario([(loop, ROAD)])

    # From the west end, turn at the crossing instead of going round the loop.
    route = shortest(built, at(3, 3.5), at(4, 4), "pedestrian")
    assert route.length_m == pytest.approx(50 + 70.7, abs=2)


def test_bridge_looping_over_itself_does_not(scenario):
    loop = [xy(3, 3), xy(4, 4), xy(4, 3.5), xy(3, 3.5)]
    built = scenario([(loop, {**ROAD, "bridge": "yes", "layer": "1"})])
    assert len(built.custom_ways) == 1

    route = shortest(built, at(3, 3.5), at(4, 4), "pedestrian")
    assert route.length_m > 140


def test_custom_bridge_over_a_custom_road(scenario):
    built = scenario([([xy(3, 3), xy(4, 4)], ROAD),
                      ([xy(3, 4), xy(4, 3)], {**ROAD, "bridge": "yes", "layer": "1"})])
    road, bridge = built.custom_ways

    assert not set(built.after.ways[road][0]) & set(built.after.ways[bridge][0])
    # Between the two only by way of a street, not at the crossing (85 m).
    route = shortest(built, at(3.2, 3.2), at(3.8, 3.2), "pedestrian")
    assert route.length_m > 150


# ---------------------------------------------------------------------
# What a line must not join, or must respect
# ---------------------------------------------------------------------

def test_road_across_the_motorway_has_no_junction_with_it(scenario):
    built = scenario([([xy(4, 1.5), xy(6, 1.5)], ROAD)])
    motorway = next(way for way, (_, tags) in built.after.ways.items()
                    if tags["highway"] == "motorway")

    assert built.after.ways[motorway][0] == built.before.ways[motorway][0]
    onto_motorway = shortest(built, at(4, 1.5), at(5, 4))
    assert not onto_motorway.uses(built.custom_ways[0])


def test_slip_road_ending_on_the_motorway_joins_it(scenario):
    built = scenario([([xy(4, 1), xy(5, 2)], {"highway": "motorway_link"})])
    (slip,) = built.custom_ways
    motorway = next(way for way, (_, tags) in built.after.ways.items()
                    if tags["highway"] == "motorway")

    on = shortest(built, at(4, 1), at(5, 4))
    assert on.way_ids == (slip, motorway) and on.length_m == pytest.approx(341, abs=2)
    off = shortest(built, at(5, 0), at(4, 1))
    assert off.way_ids == (motorway, slip)
    # Walkers can't use either.
    assert not shortest(built, at(4, 1), at(4, 2), "pedestrian").uses(slip)


def test_crossing_a_footway_lets_walkers_change_but_not_cars(scenario):
    """The grid's footway runs between nodes 8 and 13; the new road crosses it."""
    built = scenario([([xy(1, 1.5), xy(3, 1.5)], ROAD)])
    (road,) = built.custom_ways
    footway = next(way for way, (_, tags) in built.after.ways.items()
                   if tags["highway"] == "footway")
    assert len(built.after.ways[footway][0]) == 3

    walking = shortest(built, at(2, 1), at(2.5, 1.5), "pedestrian")
    assert walking.way_ids == (footway, road)
    assert walking.length_m == pytest.approx(100, abs=2)
    driving = shortest(built, at(2, 1), at(2.5, 1.5))
    assert not driving.uses(footway) and driving.length_m == pytest.approx(200, abs=2)


def test_line_through_a_bollard_is_closed_to_cars_there(scenario):
    """Node 3 is a bollard: a new road starting on it is reachable by bike, not by car."""
    built = scenario([([xy(2, 0), xy(2.5, 0.5), xy(3, 1)], ROAD)])
    (way,) = built.custom_ways

    assert shortest(built, at(1, 0), at(3, 1), "bicycle").uses(way)
    assert not shortest(built, at(1, 0), at(3, 1), "auto").uses(way)


def test_turn_restriction_survives_a_new_junction_on_its_way(scenario):
    """
    A new road crosses Row 3 Street between nodes 17 and 18, on the
    "from" way of NO_LEFT_TURN. The way keeps its id, so the restriction
    still applies - also to traffic joining from the new road.
    """
    (from_u, _), via, (_, to_v) = NO_LEFT_TURN
    built = scenario([([xy(1.5, 2.5), xy(1.5, 3.5)], ROAD)])
    start, end = built.after.node(from_u), built.after.node(to_v)
    through_via = {way for way, (refs, _) in built.after.ways.items() if via in refs}

    from_the_west = shortest(built, start, end)
    from_the_new_road = shortest(built, at(1.5, 2.5), end)
    on_foot = shortest(built, at(1.5, 2.5), end, "pedestrian")

    assert not set(from_the_west.way_ids) <= through_via
    assert from_the_new_road.length_m == pytest.approx(300, abs=2)  # round the block
    assert on_foot.length_m == pytest.approx(200, abs=2)             # straight there


def test_new_road_past_the_bus_gate_opens_it_to_cars(scenario):
    """Row 4 Street between nodes 21 and 22 is closed to motor vehicles."""
    built = scenario([([xy(0, 4), xy(0.5, 4.4), xy(1, 4)], ROAD)])

    assert shortest(built, at(0, 4), at(1, 4), which="before").length_m > 250
    assert shortest(built, at(0, 4), at(1, 4)).length_m == pytest.approx(128, abs=2)


def test_disconnected_line_is_written_but_unreachable(scenario, caplog):
    with caplog.at_level(logging.WARNING):
        built = scenario([([xy(1, 1), xy(2, 2)], ROAD), ([xy(3.3, 3.3), xy(3.6, 3.6)], ROAD)])
    joined, island = built.custom_ways

    assert not set(built.after.ways[island][0]) & junctions(built)
    assert shortest(built, at(3.3, 3.3), at(3.6, 3.6)).way_ids == (island,)
    assert shortest(built, at(3.45, 3.45), at(1, 1)) is None
    assert shortest(built, at(0, 1), at(3, 2)).uses(joined)
