"""
Points in the custom layer: node tags (barriers, signals ...) on new
lines, on existing streets and at existing junctions, as Valhalla obeys
them. Node 3 (B on the grid) is an existing bollard.
"""

import pytest

from tests.valhalla.conftest import at, turn_metres, xy

# Row 1 Street, nodes 6 to 9: 300 m along it, 500 m round the block.
WEST, EAST = at(0, 1), at(3, 1)
VEHICLES = ("auto", "bus", "truck")


def metres(router, start, end, costing):
    route = router.route(start, end, costing, shortest=True)
    return None if route is None else round(route.length_m)


def point(column, row):
    """A point feature's coordinates (the scenario takes [coords])."""
    return [xy(column, row)]


def test_bollard_mid_block_on_an_existing_street(scenario):
    built = scenario([(point(1.5, 1), {"barrier": "bollard"})])
    for costing in VEHICLES:
        assert metres(built.before, WEST, EAST, costing) == 300
        assert metres(built.after, WEST, EAST, costing) == 500, costing
    for costing in ("bicycle", "pedestrian"):
        assert metres(built.after, WEST, EAST, costing) == 300


def test_bollard_snapped_onto_the_street_from_half_a_metre_away(scenario):
    built = scenario([(point(1.5, 1.005), {"barrier": "bollard"})])
    assert metres(built.after, WEST, EAST, "auto") == 500


def test_bollard_on_a_new_line(scenario):
    """A new link across the block from node 7 to node 13 (141 m), filtered for cars."""
    link = [xy(1, 1), xy(2, 2)]
    plain = scenario([(link, {"highway": "residential"})])
    filtered = scenario([(link, {"highway": "residential"}),
                         (point(1.5, 1.5), {"barrier": "bollard"})])

    assert metres(plain.after, at(1, 1), at(2, 2), "auto") == pytest.approx(141, abs=2)
    assert metres(filtered.after, at(1, 1), at(2, 2), "auto") == 200
    assert metres(filtered.after, at(1, 1), at(2, 2), "bicycle") == pytest.approx(141, abs=2)


@pytest.mark.parametrize("tags, closed_to", [
    ({"barrier": "gate"}, ()),  # a gate is open unless tagged otherwise
    ({"barrier": "gate", "access": "no"}, (*VEHICLES, "bicycle", "pedestrian")),
    ({"barrier": "lift_gate", "motor_vehicle": "no"}, VEHICLES),
    ({"barrier": "cycle_barrier"}, VEHICLES),
    ({"barrier": "block"}, VEHICLES),
    ({"barrier": "bollard", "motor_vehicle": "yes"}, ()),
    # Valhalla 3.9 lets everyone through a private gate.
    ({"barrier": "gate", "access": "private"}, ()),
])
def test_barriers_as_valhalla_reads_them(scenario, tags, closed_to):
    built = scenario([(point(1.5, 1), tags)])
    for costing in (*VEHICLES, "bicycle", "pedestrian"):
        expected = 500 if costing in closed_to else 300
        assert metres(built.after, WEST, EAST, costing) == expected, costing


def test_existing_bollard_taken_out(scenario):
    """Row 0 Street's bollard at node 3 (B): remove_tags=barrier opens it to cars."""
    built = scenario([(point(2, 0), {"remove_tags": "barrier"})])
    start, end = at(1, 0), at(3, 0)
    assert metres(built.before, start, end, "auto") > 200
    assert metres(built.after, start, end, "auto") == 200


def test_traffic_signals_at_an_existing_junction_cost_time(scenario):
    """Straight through node 8 on Row 1 Street: same way, a little longer."""
    built = scenario([(point(2, 1), {"highway": "traffic_signals"})])
    before = built.before.route(WEST, EAST, "auto", shortest=True)
    after = built.after.route(WEST, EAST, "auto", shortest=True)

    assert after.length_m == pytest.approx(before.length_m)
    assert after.time_s > before.time_s


def test_the_before_network_has_none_of_it(scenario):
    built = scenario([(point(1.5, 1), {"barrier": "bollard"})])
    assert metres(built.before, WEST, EAST, "auto") == 300


def test_turn_restriction_holds_when_a_point_cuts_its_street(scenario):
    """The grid's no-left-turn at node 18 arrives along Row 3 Street: signals cut it."""
    built = scenario([(point(1.75, 3), {"highway": "traffic_signals"})])
    assert turn_metres(built.before, "auto") == pytest.approx(300, abs=2)
    assert turn_metres(built.after, "auto") == pytest.approx(300, abs=2)
