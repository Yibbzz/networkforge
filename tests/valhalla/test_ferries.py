"""
New ferries drawn in the custom layer: route=ferry, no highway tag.
Valhalla sails them like OSM's own; they join the streets only at their
ends, passing over the junctions they cross (a ferry has no stops on the
way).
"""

import pytest

from tests.valhalla.conftest import at, xy

# Corner to corner across the grid: 566 m by ferry, 800 m by street. It
# passes exactly over nodes 7, 13 and 19.
ACROSS = [xy(0, 0), xy(4, 4)]
FERRY = {"route": "ferry", "name": "New Ferry"}
GRID_NODES = set(range(1, 26))


def sail(built, costing):
    return built.after.route(at(0, 0), at(4, 4), costing, shortest=True)


@pytest.mark.parametrize("costing", ["pedestrian", "bicycle", "auto"])
def test_a_new_ferry_is_sailed_by_every_mode(scenario, costing):
    built = scenario([(ACROSS, FERRY)])
    (ferry,) = built.custom_ways

    route = sail(built, costing)
    assert route.way_ids == (ferry,) and route.names == ("New Ferry",)
    assert route.length_m == pytest.approx(565.7, abs=2)
    assert built.before.route(at(0, 0), at(4, 4), costing, shortest=True).length_m == (
        pytest.approx(800, abs=2))


def test_a_new_ferry_stops_only_at_its_ends(scenario):
    built = scenario([(ACROSS, FERRY)])
    (ferry,) = built.custom_ways
    refs, tags = built.after.ways[ferry]

    assert set(refs) & GRID_NODES == {1, 25}
    assert tags["route"] == "ferry" and "highway" not in tags


def test_a_foot_ferry_carries_no_cars(scenario):
    built = scenario([(ACROSS, {**FERRY, "motor_vehicle": "no"})])
    (ferry,) = built.custom_ways

    assert sail(built, "pedestrian").way_ids == (ferry,)
    assert not sail(built, "auto").uses(ferry)
    assert sail(built, "auto").length_m == pytest.approx(800, abs=2)


def test_duration_sets_the_crossing_time(scenario):
    """Valhalla's ferry speed is 10 km/h, or the way's length over its duration."""
    default = sail(scenario([(ACROSS, FERRY)]), "pedestrian")
    timed = sail(scenario([(ACROSS, {**FERRY, "duration": "00:02"})]), "pedestrian")

    assert default.time_s == pytest.approx(565.7 / (10 / 3.6), rel=0.05)
    # 566 m in 2 minutes is 16.97 km/h; Valhalla keeps whole km/h (16).
    assert timed.time_s == pytest.approx(565.7 / (16 / 3.6), rel=0.01)


def test_a_ferry_ending_next_to_a_street_joins_it(scenario):
    """Its end within snap_tolerance of a street (mid-block, 0.5 m off) joins it there."""
    pier = [xy(0, 0), xy(3.5, 3.995)]  # ends 0.5 m south of Row 4 Street, mid-block
    built = scenario([(pier, FERRY)])
    (ferry,) = built.custom_ways

    route = built.after.route(at(0, 0), at(4, 4), "pedestrian", shortest=True)
    assert route.way_ids[0] == ferry
    assert route.length_m == pytest.approx(531.5 + 50, abs=2)
