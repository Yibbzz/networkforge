"""
Public transport in R5: the walks to and from the stops follow the
streets of the network the PBF holds.

The GTFS feed (conftest.gtfs_feed) is a bus up column 1, from row 0 to
row 7 in 2 minutes, every 5 minutes.
"""

from tests.r5.conftest import N, at, row_way, xy

# From 30 m north of the southern stop to two blocks east of the
# northern stop, along row 7.
START, END = at(1, 0.3), at(3, N - 1)


def test_the_walk_from_the_stop_follows_the_after_network(scenario):
    # Row 7 is taken out from the northern stop to the end of the trip:
    # the walk from the stop goes round by row 6.
    built = scenario([([xy(1, N - 1), xy(3, N - 1)], {"osm_id": row_way(N - 1), "remove": "yes"})],
                     transit=True)

    before, after = built.minutes(START, END, "transit")
    walking = built.after.minutes(START, END, "walk")

    # From the stop: 200 m on foot before, 400 m after (about 3 minutes more).
    assert 2 <= after - before <= 5
    # The bus still beats walking the whole 870 m: R5 really took it.
    assert after < walking
