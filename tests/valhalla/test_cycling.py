"""
Cycling infrastructure, proved in Valhalla: does a cycle lane, a track or
a cycleway change the route a cyclist is given?

Two new streets join the same two junctions of the grid:

    row 3        17 ------- 18 -- END      STRAIGHT: 141 m, corner to corner
                 |  .----'/  |              DETOUR:   172 m, bulging out
                 | / .--'    |
    row 2  START 12 -------- 13

Left alone, everybody takes STRAIGHT. A cyclist who takes DETOUR, 31 m
(13%) further, does so because of what is on it. These are fastest
routes, not shortest ones: Valhalla's taste in streets is what is tested.

The grid is in Scotland, so Valhalla has traffic on the left: there
`cycleway:left` is the lane in the direction the line is drawn.

Valhalla weighs a route by its `cost`: the time it takes, raised by what
the traveller dislikes (for a cyclist, sharing a road with cars) and
lowered by what they like.
"""

import pytest

from tests.integration.test_edits import ROW_1
from tests.valhalla.conftest import at, xy

STRAIGHT = [xy(1, 2), xy(2, 3)]
DETOUR = [xy(1, 2), xy(1.15, 2.85), xy(2, 3)]
START, END = at(0.5, 2), at(2.5, 3)
STREET = {"highway": "residential", "maxspeed": "30 mph"}


def choice(scenario, on_detour, on_straight=None, costing="bicycle", start=START, end=END,
           **options) -> str:
    """Which of the two streets the fastest route takes: "detour" or "straight"."""
    built = scenario([(STRAIGHT, {**STREET, **(on_straight or {})}),
                      (DETOUR, {**STREET, **on_detour})], strict=False)
    straight, detour = built.custom_ways
    route = built.after.route(start, end, costing, **options)
    assert route.uses(straight) != route.uses(detour), route
    return "detour" if route.uses(detour) else "straight"


@pytest.mark.parametrize("costing", ["bicycle", "auto", "pedestrian"])
def test_two_plain_streets_everyone_takes_the_shorter(scenario, costing):
    assert choice(scenario, {}, costing=costing) == "straight"
    assert choice(scenario, {}, costing=costing, start=END, end=START) == "straight"


# ---------------------------------------------------------------------
# What draws a cyclist off the shorter street
# ---------------------------------------------------------------------

@pytest.mark.parametrize("infrastructure", [
    {"cycleway": "lane"},        # a painted lane on the street
    {"cycleway": "track"},       # a track beside it, apart from the traffic
    {"highway": "cycleway"},     # no street at all: a cycleway of its own
])
def test_cycling_infrastructure_draws_cyclists_and_nobody_else(scenario, infrastructure):
    assert choice(scenario, infrastructure) == "detour"
    assert choice(scenario, infrastructure, start=END, end=START) == "detour"
    for costing in ("auto", "pedestrian"):
        assert choice(scenario, infrastructure, costing=costing) == "straight"


def test_a_track_counts_for_more_than_a_lane(scenario):
    def cost(infrastructure):
        built = scenario([(STRAIGHT, STREET), (DETOUR, {**STREET, **infrastructure})],
                         strict=False)
        return built.after.route(START, END, "bicycle").cost

    assert cost({"cycleway": "track"}) < cost({"cycleway": "lane"})


@pytest.mark.parametrize("side, with_the_line, against_it", [
    ("left", "detour", "straight"),    # traffic on the left: the lane the line's way
    ("right", "straight", "detour"),
])
def test_a_track_on_one_side_serves_one_direction(scenario, side, with_the_line, against_it):
    track = {f"cycleway:{side}": "track"}
    assert choice(scenario, track) == with_the_line
    assert choice(scenario, track, start=END, end=START) == against_it


# ---------------------------------------------------------------------
# Tags that are written but that Valhalla 3.9 doesn't act on
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tags", [
    {"cycleway:both": "lane"},      # use cycleway=lane, or :left and :right
    {"cycleway": "shared_lane"},    # read (as a "shared" lane), too weak to move a route
    {"bicycle_road": "yes"},
    {"cyclestreet": "yes"},
])
def test_tags_valhalla_does_not_route_by(scenario, tags):
    """If one of these starts to pass as "detour", Valhalla has learnt it: say so in
    docs/network-analyst.md and move it to the test above."""
    built = scenario([(STRAIGHT, STREET), (DETOUR, {**STREET, **tags})], strict=False)
    detour = built.custom_ways[1]

    assert tags.items() <= built.after.ways[detour][1].items()   # it is in the file
    assert choice(scenario, tags) == "straight"


# ---------------------------------------------------------------------
# A lane added to a street that is already there
# ---------------------------------------------------------------------

def test_a_lane_added_to_an_existing_street_makes_it_cheaper_to_cycle(scenario):
    block = [xy(1, 1), xy(2, 1)]            # of Row 1 Street
    west, east = at(0, 1), at(3, 1)
    built = scenario([(block, {"osm_id": ROW_1, "cycleway": "lane"})])

    def along_row_1(which, costing):
        route = built.route(which, west, east, costing)
        assert route.length_m == pytest.approx(300, abs=2)
        return route

    assert along_row_1("after", "bicycle").cost < along_row_1("before", "bicycle").cost
    assert along_row_1("after", "bicycle").time_s == along_row_1("before", "bicycle").time_s
    for costing in ("auto", "pedestrian"):
        assert along_row_1("after", costing).cost == along_row_1("before", costing).cost


# ---------------------------------------------------------------------
# The cyclist: Valhalla's settings for who is riding
# ---------------------------------------------------------------------

def test_a_cyclist_at_ease_on_roads_does_not_go_out_of_the_way_for_a_cycleway(scenario):
    cycleway = {"highway": "cycleway"}
    assert choice(scenario, cycleway, use_roads=0.0) == "detour"
    assert choice(scenario, cycleway) == "detour"                  # Valhalla's own 0.5
    assert choice(scenario, cycleway, use_roads=1.0) == "straight"


@pytest.mark.parametrize("surface, can_ride_it", [
    ("compacted", {"hybrid", "cross", "mountain"}),
    ("dirt", {"cross", "mountain"}),
    ("gravel", {"mountain"}),
])
def test_an_unpaved_street_is_only_for_the_bicycles_that_can_take_it(
        scenario, surface, can_ride_it):
    """A new gravel path changes nothing for Valhalla's default cyclist, who rides a
    hybrid: it is closed to that bicycle, not just slower."""
    built = scenario([(STRAIGHT, {**STREET, "surface": surface})])
    (street,) = built.custom_ways

    for bicycle_type in ("road", "hybrid", "cross", "mountain"):
        route = built.after.route(START, END, "bicycle", bicycle_type=bicycle_type)
        rides_it = bicycle_type in can_ride_it
        assert route.uses(street) is rides_it, bicycle_type
        # Along the new street, or round by the streets that were there.
        assert route.length_m == pytest.approx(241 if rides_it else 300, abs=2)
    # The default is the hybrid.
    assert built.after.route(START, END, "bicycle").uses(street) is ("hybrid" in can_ride_it)
