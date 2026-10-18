"""
Changes to existing streets in R5: a street taken out, a street made
one-way, a bollard.
"""

import pytest

from tests.r5.conftest import at, row_way, xy

# The middle block of row 0, drawn west to east, and a trip along row 0
# from half-way along block 1 to half-way along block 5: 400 m straight
# on, 600 m round by row 1 when the middle block can't be used.
MIDDLE_BLOCK = [xy(3, 0), xy(4, 0)]
A, B = at(1.5, 0), at(5.5, 0)


def test_a_removed_street_sends_everyone_round(scenario):
    built = scenario([(MIDDLE_BLOCK, {"osm_id": row_way(0), "remove": "yes"})])

    walking = built.minutes(A, B, "walk")
    driving = built.minutes(A, B, "car")

    # At 3.6 km/h: 400 m is 6-7 minutes, 600 m 10.
    assert 6 <= walking[0] <= 7 and 9 <= walking[1] <= 11
    assert driving[1] > driving[0]


def test_a_street_made_one_way_runs_the_way_the_line_is_drawn(scenario):
    # Drawn east to west: cars may only go west along the middle block.
    built = scenario([(MIDDLE_BLOCK[::-1], {"osm_id": row_way(0), "oneway": "yes"})])

    eastward = built.minutes(A, B, "car")
    westward = built.minutes(B, A, "car")

    assert eastward[1] > eastward[0]
    assert westward[1] == westward[0]


def test_a_street_closed_to_walkers_is_walked_round_and_still_driven(scenario):
    # Esri's "RestrictPedestrians" in its public transit tutorial.
    built = scenario([(MIDDLE_BLOCK, {"osm_id": row_way(0), "foot": "no"})])

    walking = built.minutes(A, B, "walk")
    driving = built.minutes(A, B, "car")

    assert 6 <= walking[0] <= 7 and 9 <= walking[1] <= 11
    assert driving[1] == driving[0]


@pytest.mark.xfail(strict=True, reason=(
    "R5 ignores barriers: none of R5's classes reads the barrier tag. The bollard is in "
    "the PBF, and Valhalla obeys it (tests/valhalla/test_points.py)."))
def test_a_bollard_stops_cars_not_walkers(scenario):
    built = scenario([([xy(3.5, 0)], {"barrier": "bollard"})])

    walking = built.minutes(A, B, "walk")
    driving = built.minutes(A, B, "car")

    assert walking[1] == walking[0]
    assert driving[1] > driving[0]
