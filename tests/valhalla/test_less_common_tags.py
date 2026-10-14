"""
Tags that few proposals need, proved in Valhalla all the same: other
vehicles (motorcycles, mopeds, taxis), buses against a one-way street,
roundabouts, speeds for lorries, rough surfaces and mountain paths.

One new street across a block of the grid (SHORTCUT in
test_esri_network_dataset.py). A trip takes it or goes round by the
streets, in each direction.
"""

import pytest

from tests.valhalla.test_esri_network_dataset import FROM, ROAD, SHORTCUT, TO

VEHICLES = ("auto", "bus", "truck", "taxi", "motorcycle", "motor_scooter")
EVERYONE = (*VEHICLES, "bicycle", "pedestrian")


def users(scenario, tags, start=FROM, end=TO, **options) -> set[str]:
    """The travel modes whose shortest route from start to end takes the new street."""
    built = scenario([(SHORTCUT, tags)], strict=False)
    way = built.custom_ways[0]
    return {costing for costing in EVERYONE
            if built.after.route(start, end, costing, shortest=True, **options).uses(way)}


def seconds(scenario, tags, costing, start=FROM, end=TO) -> float:
    built = scenario([(SHORTCUT, tags)], strict=False)
    route = built.after.route(start, end, costing, shortest=True)
    assert route.uses(built.custom_ways[0])
    return route.time_s


# ---------------------------------------------------------------------
# Other vehicles
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tags, kept_out", [
    ({}, set()),
    ({"motorcycle": "no"}, {"motorcycle"}),
    ({"moped": "no"}, {"motor_scooter"}),
    ({"taxi": "no"}, {"taxi"}),
    ({"motor_vehicle": "no", "taxi": "yes"}, set(VEHICLES) - {"taxi"}),
])
def test_access_for_motorcycles_mopeds_and_taxis(scenario, tags, kept_out):
    assert users(scenario, {**ROAD, **tags}) == set(EVERYONE) - kept_out


@pytest.mark.parametrize("exception, against_the_flow", [
    ("oneway:bus", {"bus", "pedestrian"}),
    ("oneway:psv", {"bus", "taxi", "pedestrian"}),      # psv: buses and taxis
])
def test_buses_against_a_one_way_street(scenario, exception, against_the_flow):
    tags = {**ROAD, "oneway": "yes", exception: "no"}

    assert users(scenario, tags) == set(EVERYONE)
    assert users(scenario, tags, start=TO, end=FROM) == against_the_flow


def test_a_roundabout_is_one_way_the_way_it_is_drawn(scenario):
    """`junction=roundabout` needs no `oneway`: draw it in the direction of travel."""
    tags = {**ROAD, "junction": "roundabout"}

    assert users(scenario, tags) == set(EVERYONE)
    assert users(scenario, tags, start=TO, end=FROM) == {"pedestrian"}


# ---------------------------------------------------------------------
# Speeds
# ---------------------------------------------------------------------

def test_a_lower_limit_for_lorries_slows_lorries_only(scenario):
    tags = {**ROAD, "maxspeed:hgv": "10 mph"}

    assert seconds(scenario, tags, "truck") > seconds(scenario, ROAD, "truck") * 1.5
    assert seconds(scenario, tags, "auto") == seconds(scenario, ROAD, "auto")


def test_a_limit_for_one_direction_slows_both(scenario):
    """Valhalla 3.9 applies `maxspeed:forward` to both directions. If this fails it has
    learnt to tell them apart: say so in docs/network-analyst.md."""
    tags = {**ROAD, "maxspeed:forward": "10 mph"}

    with_the_line = seconds(scenario, tags, "auto")
    against_it = seconds(scenario, tags, "auto", start=TO, end=FROM)
    assert with_the_line == against_it > seconds(scenario, ROAD, "auto") * 1.5


@pytest.mark.parametrize("tags", [
    {"highway": "living_street"},
    {"highway": "service", "service": "alley"},
])
def test_living_streets_and_alleys_are_slower_to_drive(scenario, tags):
    assert users(scenario, tags) == set(EVERYONE)
    assert seconds(scenario, tags, "auto") > seconds(scenario, ROAD, "auto") * 1.2


# ---------------------------------------------------------------------
# Rough going
# ---------------------------------------------------------------------

def test_an_impassable_street_is_closed_to_everyone(scenario):
    assert users(scenario, {**ROAD, "smoothness": "impassable"}) == set()


def test_a_horrible_surface_keeps_mopeds_off_and_slows_bicycles(scenario):
    tags = {**ROAD, "smoothness": "horrible"}

    assert users(scenario, tags) == set(EVERYONE) - {"motor_scooter"}
    assert seconds(scenario, tags, "bicycle") > seconds(scenario, ROAD, "bicycle") * 1.3


def test_a_rough_track_is_slow_to_drive(scenario):
    track = {"highway": "track", "tracktype": "grade5"}

    assert users(scenario, track) == set(EVERYONE) - {"motor_scooter"}
    assert seconds(scenario, track, "auto") > seconds(scenario, ROAD, "auto") * 5


def test_a_mountain_path_is_for_walkers_who_ask_for_it(scenario):
    path = {"highway": "path", "sac_scale": "demanding_mountain_hiking"}

    assert users(scenario, path) == {"bicycle"}     # not even on foot, as it stands
    assert "pedestrian" in users(scenario, path, max_hiking_difficulty=6)


# ---------------------------------------------------------------------
# Written, but Valhalla 3.9 doesn't route by them
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tags", [{"ford": "yes"}, {"hov": "designated"}])
def test_tags_that_change_nothing(scenario, tags):
    """If this fails Valhalla has learnt the tag: say so in docs/network-analyst.md."""
    built = scenario([(SHORTCUT, {**ROAD, **tags})], strict=False)

    assert tags.items() <= built.after.ways[built.custom_ways[0]][1].items()
    assert users(scenario, {**ROAD, **tags}) == set(EVERYONE)
    for costing in EVERYONE:
        assert seconds(scenario, {**ROAD, **tags}, costing) == seconds(scenario, ROAD, costing)
