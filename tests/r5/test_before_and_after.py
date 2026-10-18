"""
New streets in R5: the after network is faster where a custom line
helps, for the modes allowed on it, and the same for the rest.
"""

from tests.r5.conftest import N, at, xy

# From one corner of the grid to the opposite one: 1,400 m round the
# blocks, 990 m straight across.
CORNER, FAR_CORNER = at(0, 0), at(N - 1, N - 1)
DIAGONAL = [xy(0, 0), xy(N - 1, N - 1)]


def test_a_new_cycleway_shortens_walking_and_cycling_not_driving(scenario):
    built = scenario([(DIAGONAL, {"highway": "cycleway"})])

    walking = built.minutes(CORNER, FAR_CORNER, "walk")
    cycling = built.minutes(CORNER, FAR_CORNER, "bicycle")
    driving = built.minutes(CORNER, FAR_CORNER, "car")

    # r5py walks at 3.6 km/h: 1,400 m is 23 minutes, 990 m 16-17.
    assert 21 <= walking[0] <= 25 and 15 <= walking[1] <= 18
    assert cycling[1] < cycling[0]
    # Cars may not use a cycleway.
    assert driving[1] == driving[0]


def test_a_new_road_is_used_by_cars(scenario):
    built = scenario([(DIAGONAL, {"highway": "tertiary"})])

    before, after = built.minutes(CORNER, FAR_CORNER, "car")

    # 1,400 m at the grid's 10 km/h is over 8 minutes; the new road has
    # no such limit.
    assert before >= 8
    assert after < before / 2
