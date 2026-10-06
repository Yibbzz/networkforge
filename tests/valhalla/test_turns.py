"""
Turn restrictions drawn in the custom layer, obeyed by Valhalla.

TURN_LEFT runs east along Row 1 Street and turns left (north) into
Column 1 Avenue at node 7. From half a block before the junction to half
a block after it is 100 m with the turn, and about 300 m round the block
without it.
"""

import pytest

from tests.integration.grid import ROAD_ACROSS, ROAD_TAGS
from tests.valhalla.conftest import at, turn_metres, xy

TURN_LEFT = [xy(0.5, 1), xy(1, 1), xy(1, 1.5)]
STRAIGHT_ON = [xy(0.5, 1), xy(1, 1), xy(1.5, 1)]
BEFORE, LEFT, RIGHT, AHEAD = at(0.5, 1), at(1, 1.5), at(1, 0.5), at(1.5, 1)
VEHICLES = ("auto", "bus", "truck")
EVERYONE = (*VEHICLES, "bicycle", "pedestrian")


def metres(built, which, start, end, costing):
    return round(built.route(which, start, end, costing, shortest=True).length_m)


def turns_left(built, costing, which="after") -> bool:
    return metres(built, which, BEFORE, LEFT, costing) == 100


def test_banned_left_turn(scenario):
    built = scenario([(TURN_LEFT, {"restriction": "no_left_turn"})])

    for costing in EVERYONE:
        assert turns_left(built, costing, "before"), costing
    for costing in (*VEHICLES, "bicycle"):
        assert not turns_left(built, costing), costing
        assert metres(built, "after", BEFORE, LEFT, costing) > 250
    assert turns_left(built, "pedestrian")  # walkers aren't bound by turn restrictions
    # Only that turn: straight on and right are still allowed.
    assert metres(built, "after", BEFORE, AHEAD, "auto") == 100
    assert metres(built, "after", BEFORE, RIGHT, "auto") == 100


def test_only_straight_on(scenario):
    built = scenario([(STRAIGHT_ON, {"restriction": "only_straight_on"})])

    assert metres(built, "after", BEFORE, AHEAD, "auto") == 100
    assert metres(built, "after", BEFORE, LEFT, "auto") > 250
    assert metres(built, "after", BEFORE, RIGHT, "auto") > 250


def test_exceptions(scenario):
    built = scenario([(TURN_LEFT, {"restriction": "no_left_turn", "except": "bicycle"})])
    assert turns_left(built, "bicycle")
    assert not turns_left(built, "auto")


@pytest.mark.parametrize("key, banned", [
    ("restriction:hgv", {"truck"}),
    ("restriction:bus", {"bus"}),
    ("restriction:motorcar", {"auto"}),
    ("restriction:bicycle", {"bicycle"}),
])
def test_restriction_for_one_kind_of_vehicle(scenario, key, banned):
    built = scenario([(TURN_LEFT, {key: "no_left_turn"})])
    for costing in ("auto", "bus", "truck", "bicycle"):
        assert turns_left(built, costing) is (costing not in banned), costing


def test_turn_from_a_new_road_onto_an_existing_street(scenario):
    """ROAD_ACROSS meets Column 1 Avenue at a new junction (1, 1.5)."""
    turn = [xy(0.5, 1.5), xy(1, 1.5), xy(1, 1.8)]
    built = scenario([(ROAD_ACROSS, ROAD_TAGS), (turn, {"restriction": "no_left_turn"})])
    start, end = at(0.5, 1.5), at(1, 1.8)

    assert metres(built, "after", start, end, "auto") > 100
    assert metres(built, "after", start, end, "pedestrian") == pytest.approx(80, abs=2)


def test_turn_onto_a_new_road(scenario):
    """Southbound on Column 1 Avenue, no left turn (east) into the new road."""
    turn = [xy(1, 1.8), xy(1, 1.5), xy(1.5, 1.5)]
    built = scenario([(ROAD_ACROSS, ROAD_TAGS), (turn, {"restriction": "no_left_turn"})])

    assert metres(built, "after", at(1, 1.8), at(1.5, 1.5), "auto") > 80
    assert metres(built, "after", at(1, 1.8), at(1.5, 1.5), "bicycle") > 80
    assert metres(built, "after", at(1, 1.8), at(1.5, 1.5), "pedestrian") == pytest.approx(
        80, abs=2)


def test_existing_restriction_still_applies_beside_a_new_one(scenario):
    """The grid bans the left turn at node 18; add a ban on the right turn there too."""
    right_turn = [xy(1.5, 3), xy(2, 3), xy(2, 2.5)]
    built = scenario([(right_turn, {"restriction": "no_right_turn"})])

    assert turn_metres(built.after, "auto") == pytest.approx(300, abs=2)  # the left turn
    assert metres(built, "after", at(1.5, 3), at(2, 2.5), "auto") > 100   # the right turn
    assert metres(built, "after", at(1.5, 3), at(2.5, 3), "auto") == 100  # straight on


def test_before_file_has_no_custom_restriction(scenario):
    built = scenario([(TURN_LEFT, {"restriction": "no_left_turn"})])
    assert turns_left(built, "auto", "before")


def test_via_way_restriction(scenario):
    """
    No left turn from Row 2 Street (11-12) along it to 13 and north (13-18): a
    restriction over two junctions. From mid 11-12 to mid 13-18: 200 m, else 300 m.
    """
    start, end = at(0.5, 2), at(2, 2.5)
    line = [xy(0.5, 2), xy(1, 2), xy(2, 2), xy(2, 2.5)]
    built = scenario([(line, {"restriction": "no_left_turn"})])

    for costing in ("auto", "bus", "truck"):
        assert built.before.route(start, end, costing, shortest=True).length_m == (
            pytest.approx(200, abs=2))
        assert built.after.route(start, end, costing, shortest=True).length_m == (
            pytest.approx(300, abs=2)), costing
    # Turning at 12 alone, or at 13 having come from elsewhere, is still allowed.
    assert built.after.route(at(1, 2.5), end, "auto", shortest=True).length_m == (
        pytest.approx(200, abs=2))
    assert built.after.route(start, at(1, 2.5), "auto", shortest=True).length_m == (
        pytest.approx(100, abs=2))
    assert built.after.route(start, end, "pedestrian", shortest=True).length_m == (
        pytest.approx(200, abs=2))
