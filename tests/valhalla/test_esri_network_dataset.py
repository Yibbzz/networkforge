"""
Everything Esri's "Create a network dataset" tutorial sets up by hand,
checked in Valhalla on a network NetworkForge built:
https://doc.esri.com/en/arcgis-pro/latest/help/analysis/networks/how-to-create-a-usable-network-dataset.html

Each section names the tutorial's step and the OSM tags that replace it
(the table in docs/network-analyst.md). One custom line carries the tags
under test: SHORTCUT, a diagonal across a block of the grid

    row 2   12 --- 13 --- 14          FROM = node 6, TO = node 14
             |    / f      |          via the streets: 400 m
    row 1    6 -- 7 ------ 8          via SHORTCUT (7 -> 13): 341 m

so a route that takes the custom line is 341 m long and runs along its way.
Routes start and end a block away from the line: Valhalla doesn't apply
restrictions to the edge a trip starts or ends on. `shortest=True`
compares pure distance, so a result never hangs on Valhalla's taste in
roads (it prefers main roads and avoids turns).
"""

import pytest

from tests.integration.grid import NO_LEFT_TURN, RESTRICTION_ID
from tests.valhalla.conftest import at, xy

SHORTCUT = [xy(1, 1), xy(2, 2)]
FROM, TO = at(0, 1), at(3, 2)
VIA_STREETS, VIA_SHORTCUT = 400, 341

ROAD = {"highway": "residential", "maxspeed": "30 mph"}
EVERYONE = ("auto", "bus", "truck", "bicycle", "pedestrian")


def takes_shortcut(scenario, tags, costing, start=FROM, end=TO, **options) -> bool:
    """Does the shortest route on the after network (SHORTCUT tagged `tags`) use it?"""
    built = scenario([(SHORTCUT, tags)])
    route = built.after.route(start, end, costing, shortest=True, **options)
    used = route.uses(built.custom_ways[0])
    assert route.length_m == pytest.approx(VIA_SHORTCUT if used else VIA_STREETS, abs=2)
    return used


def test_before_network_has_no_shortcut(scenario):
    built = scenario([(SHORTCUT, ROAD)])
    for costing in EVERYONE:
        route = built.before.route(FROM, TO, costing, shortest=True)
        assert route.length_m == pytest.approx(VIA_STREETS, abs=2)


# ---------------------------------------------------------------------
# Sources: streets and walking paths in one network
# ---------------------------------------------------------------------

@pytest.mark.parametrize("costing", EVERYONE)
def test_a_new_street_is_used_by_every_travel_mode(scenario, costing):
    assert takes_shortcut(scenario, ROAD, costing)


@pytest.mark.parametrize("costing, expected", [
    ("pedestrian", True), ("auto", False), ("bus", False), ("truck", False), ("bicycle", False),
])
def test_a_walking_path_is_used_on_foot_only(scenario, costing, expected):
    assert takes_shortcut(scenario, {"highway": "footway"}, costing) is expected


# ---------------------------------------------------------------------
# Vertical connectivity (Esri: F_ZLEV / T_ZLEV) -> bridge, tunnel, layer
# ---------------------------------------------------------------------

OVER_NODE_12 = [xy(0, 1), xy(2, 3)]  # node 6 to node 18, straight over junction 12


@pytest.mark.parametrize("tags, joins", [
    ({}, True),
    ({"bridge": "yes", "layer": "1"}, False),
    ({"tunnel": "yes", "layer": "-1"}, False),
    ({"layer": "1"}, False),
])
def test_a_bridge_or_tunnel_crosses_a_junction_without_joining_it(scenario, tags, joins):
    built = scenario([(OVER_NODE_12, {"highway": "primary", **tags})])

    end_to_end = built.after.route(at(0, 1), at(2, 3), "auto", shortest=True)
    from_underneath = built.after.route(at(1, 2), at(2, 3), "auto", shortest=True)

    assert end_to_end.length_m == pytest.approx(283, abs=2)  # the new road either way
    assert from_underneath.length_m == pytest.approx(141 if joins else 200, abs=2)
    assert from_underneath.uses(built.custom_ways[0]) is joins


# ---------------------------------------------------------------------
# Costs: distance, time, turn delays
# ---------------------------------------------------------------------

def test_distance_cost_is_the_length_of_the_line(scenario):
    built = scenario([(SHORTCUT, ROAD)])
    along = built.after.route(at(1, 1), at(2, 2), "pedestrian", shortest=True)
    assert along.length_m == pytest.approx(2 ** 0.5 * 100, abs=1)


@pytest.mark.parametrize("maxspeed, kph", [("20 mph", 32.2), ("50", 50), ("60 mph", 96.6)])
def test_time_cost_follows_the_speed_limit(scenario, maxspeed, kph):
    built = scenario([(SHORTCUT, {"highway": "primary", "maxspeed": maxspeed})])
    along = built.after.route(at(1, 1), at(2, 2), "auto")
    assert along.time_s == pytest.approx(141.4 / (kph / 3.6), rel=0.05)


def test_without_a_speed_limit_the_road_class_sets_the_speed(scenario):
    def seconds(highway):
        built = scenario([(SHORTCUT, {"highway": highway})])
        return built.after.route(at(1, 1), at(2, 2), "auto").time_s

    assert seconds("primary") < seconds("tertiary") < seconds("residential")


def test_turning_costs_time(scenario):
    """Esri's turn category evaluator; Valhalla adds turn delays itself."""
    built = scenario([(SHORTCUT, ROAD)])
    straight = built.before.route(at(0, 0), at(2, 0), "auto")
    with_a_turn = built.before.route(at(0, 1), at(1, 2), "auto")

    assert straight.length_m == pytest.approx(with_a_turn.length_m, abs=2)
    assert with_a_turn.time_s > straight.time_s + 1


# ---------------------------------------------------------------------
# Restrictions: who may drive, ride a bus, walk (Esri: AR_AUTO, AR_BUS, AR_PEDEST)
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tags, allowed", [
    ({"motor_vehicle": "no"}, {"bicycle", "pedestrian"}),
    ({"access": "no", "bus": "yes"}, {"bus"}),
    ({"highway": "busway"}, {"bus"}),
    ({"motor_vehicle": "no", "psv": "yes"}, {"bus", "bicycle", "pedestrian"}),
    ({"hgv": "no"}, {"auto", "bus", "bicycle", "pedestrian"}),
    ({"foot": "no"}, {"auto", "bus", "truck", "bicycle"}),
    ({"bicycle": "no"}, {"auto", "bus", "truck", "pedestrian"}),
    ({"access": "no", "foot": "yes"}, {"pedestrian"}),
    ({"vehicle": "no"}, {"pedestrian"}),
    ({"highway": "trunk", "motorroad": "yes"}, {"auto", "bus", "truck"}),
])
def test_access_tags_restrict_travel_modes(scenario, tags, allowed):
    for costing in EVERYONE:
        assert takes_shortcut(scenario, {**ROAD, **tags}, costing) is (costing in allowed), costing


# ---------------------------------------------------------------------
# One-way streets (Esri: DIR_TRAVEL)
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tags, forward, backward", [
    ({"oneway": "yes"}, {"auto", "bus", "truck", "bicycle", "pedestrian"}, {"pedestrian"}),
    ({"oneway": "-1"}, {"pedestrian"}, {"auto", "bus", "truck", "bicycle", "pedestrian"}),
    ({"oneway": "no"}, set(EVERYONE), set(EVERYONE)),
    ({"oneway": "yes", "oneway:bicycle": "no"}, set(EVERYONE), {"bicycle", "pedestrian"}),
    ({"oneway": "yes", "cycleway": "opposite"}, set(EVERYONE), {"bicycle", "pedestrian"}),
])
def test_one_way_follows_the_direction_the_line_was_drawn(scenario, tags, forward, backward):
    """SHORTCUT is drawn from node 7 to node 13; walking ignores one-way."""
    for costing in EVERYONE:
        there = takes_shortcut(scenario, {**ROAD, **tags}, costing)
        back = takes_shortcut(scenario, {**ROAD, **tags}, costing, start=TO, end=FROM)
        assert there is (costing in forward), costing
        assert back is (costing in backward), costing


# ---------------------------------------------------------------------
# "Avoid unpaved roads" (Esri: PAVED) -> surface
# ---------------------------------------------------------------------

def test_an_unpaved_road_is_slower_and_can_be_excluded(scenario):
    gravel = {"highway": "residential", "surface": "gravel"}
    paved = scenario([(SHORTCUT, ROAD)]).after.route(at(1, 1), at(2, 2), "auto")
    unpaved = scenario([(SHORTCUT, gravel)]).after.route(at(1, 1), at(2, 2), "auto")

    assert unpaved.time_s > paved.time_s * 1.5
    for costing in ("auto", "bus", "truck"):
        assert takes_shortcut(scenario, gravel, costing)
        assert not takes_shortcut(scenario, gravel, costing, exclude_unpaved=True)


def test_fastest_and_shortest_travel_modes_differ(scenario):
    """Esri's "Automobile Time" vs "Automobile Distance" travel modes."""
    built = scenario([(SHORTCUT, {"highway": "residential", "surface": "gravel"})])
    fastest = built.after.route(FROM, TO, "auto")
    shortest = built.after.route(FROM, TO, "auto", shortest=True)

    assert shortest.length_m == pytest.approx(VIA_SHORTCUT, abs=2)
    assert fastest.length_m == pytest.approx(VIA_STREETS, abs=2)
    assert fastest.time_s < shortest.time_s


# ---------------------------------------------------------------------
# Height limit and the vehicle's height (Esri: descriptor + parameter)
# ---------------------------------------------------------------------

@pytest.mark.parametrize("costing, vehicle, expected", [
    ("auto", {}, True),                     # a car fits
    ("truck", {}, False),                   # Valhalla's default lorry is 4.11 m
    ("truck", {"height": 2.5}, True),
    ("bus", {"height": 3.5}, False),        # Esri's tour bus (11 ft) meets a 3 m bridge
    ("bus", {"height": 2.9}, True),
])
def test_a_low_bridge_turns_back_tall_vehicles_only(scenario, costing, vehicle, expected):
    assert takes_shortcut(scenario, {**ROAD, "maxheight": "3"}, costing, **vehicle) is expected


@pytest.mark.parametrize("maxheight", ["3.5", "3.5 m", "11'6\"", "11.5 ft"])
def test_height_limits_are_read_in_metres_or_feet(scenario, maxheight):
    tags = {**ROAD, "maxheight": maxheight}
    assert takes_shortcut(scenario, tags, "truck", height=3.4)
    assert not takes_shortcut(scenario, tags, "truck", height=3.6)


@pytest.mark.parametrize("tag, value, option, fits, too_big", [
    ("maxweight", "7.5", "weight", 5, 10),
    ("maxweight", "7.5 t", "weight", 5, 10),
    ("maxwidth", "2", "width", 1.9, 2.2),
    ("maxlength", "8", "length", 7, 9),
    ("maxaxleload", "5", "axle_load", 4, 6),
])
def test_weight_and_size_limits(scenario, tag, value, option, fits, too_big):
    tags = {**ROAD, tag: value}
    assert takes_shortcut(scenario, tags, "truck", **{option: fits})
    assert not takes_shortcut(scenario, tags, "truck", **{option: too_big})
    assert takes_shortcut(scenario, tags, "bicycle")


def test_hazardous_loads(scenario):
    tags = {**ROAD, "hazmat": "no"}
    assert takes_shortcut(scenario, tags, "truck")
    assert not takes_shortcut(scenario, tags, "truck", hazmat=True)


def test_toll_roads_can_be_excluded(scenario):
    tags = {"highway": "primary", "toll": "yes"}
    assert takes_shortcut(scenario, tags, "auto")
    assert not takes_shortcut(scenario, tags, "auto", exclude_tolls=True)


def test_time_of_day_closure_reaches_valhalla(scenario):
    """
    Read as a timed restriction. Valhalla only applies it to trips with a
    departure time on a graph built with its time zone database, which
    is the router's side (see docs/network-analyst.md).
    """
    tags = {**ROAD, "motor_vehicle:conditional": "no @ (07:00-19:00)"}
    built = scenario([(SHORTCUT, tags)])
    (restriction,) = built.after.edges(built.custom_ways[0])[0]["access_restrictions"]

    assert restriction["type"] == "timed_denied"
    assert restriction["car"] and restriction["bus"] and not restriction["pedestrian"]


# ---------------------------------------------------------------------
# Hierarchy (Esri: FUNC_CLASS) -> the highway class
# ---------------------------------------------------------------------

@pytest.mark.parametrize("highway, road_class", [
    ("motorway", "motorway"), ("trunk", "trunk"), ("primary", "primary"),
    ("secondary", "secondary"), ("tertiary", "tertiary"),
    ("unclassified", "unclassified"), ("residential", "residential"),
    ("service", "service_other"),
])
def test_highway_sets_the_road_class(scenario, highway, road_class):
    built = scenario([(SHORTCUT, {"highway": highway})])
    for edge in built.after.edges(built.custom_ways[0]):
        assert edge["edge"]["classification"]["classification"] == road_class


# ---------------------------------------------------------------------
# Directions: street names and road numbers
# ---------------------------------------------------------------------

def test_directions_use_the_new_road_name_and_number(scenario):
    built = scenario([(SHORTCUT, {**ROAD, "name": "Forge Lane", "ref": "B 123"})])
    route = built.after.route(FROM, TO, "auto", shortest=True)

    assert route.names == ("Row 1 Street", "Forge Lane", "B 123", "Row 2 Street")


def test_signpost_text_is_in_the_directions(scenario):
    """Esri's Signposts table; in OSM a slip road's destination tags."""
    tags = {"highway": "motorway_link", "oneway": "yes",
            "destination": "Airport;City", "destination:ref": "A 1"}
    built = scenario([(SHORTCUT, tags)])
    trip = built.after.actor.route({
        "locations": [{"lon": FROM[0], "lat": FROM[1]}, {"lon": TO[0], "lat": TO[1]}],
        "costing": "auto", "costing_options": {"auto": {"shortest": True}},
    })["trip"]
    instructions = [maneuver["instruction"] for maneuver in trip["legs"][0]["maneuvers"]]

    assert any("toward A 1/Airport/City" in text for text in instructions), instructions


def test_existing_street_names_are_in_the_directions(scenario):
    built = scenario([(SHORTCUT, ROAD)])
    route = built.before.route(at(0, 0), at(4, 0), "pedestrian", shortest=True)
    assert route.names == ("Row 0 Street",)


# ---------------------------------------------------------------------
# Turn restrictions (Esri: turn feature classes) - kept from OSM
# ---------------------------------------------------------------------

def test_existing_turn_restriction_is_obeyed_before_and_after(scenario):
    """NO_LEFT_TURN: east along row 3, no turning north at node 18."""
    (from_u, _), via, (_, to_v) = NO_LEFT_TURN
    built = scenario([(SHORTCUT, ROAD)])
    start, end = built.after.node(from_u), built.after.node(to_v)

    for router in (built.before, built.after):
        assert RESTRICTION_ID  # the relation the grid defines
        driving = router.route(start, end, "auto", shortest=True)
        walking = router.route(start, end, "pedestrian", shortest=True)
        through_via = {way for way, (refs, _) in router.ways.items() if via in refs}

        assert walking.length_m == pytest.approx(200, abs=2)
        assert driving.length_m == pytest.approx(200, abs=2)
        assert set(walking.way_ids) <= through_via       # straight through the junction
        assert not set(driving.way_ids) <= through_via   # around the block instead
