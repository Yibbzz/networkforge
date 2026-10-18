"""
Cycling in R5: R5 rates each street's traffic stress (LTS) from its
tags, and by default (r5py: max_bicycle_traffic_stress=3) cyclists keep
off streets rated 4, such as a busy main road. A cycle track drawn as a
line of its own beside the road opens the trip to them; a cycle track
tagged on the road itself does not, in R5 (Valhalla reads both).
"""

import pytest

from tests.r5.conftest import N, at, row_way, xy

# Row 4 is a busy main road (conftest.BUSY). A trip along it, from
# half-way along block 0 to half-way along block 6: 600 m straight on,
# 800 m round by row 3 or row 5.
BUSY_ROW = 4
A, B = at(0.5, BUSY_ROW), at(6.5, BUSY_ROW)
SLOW = {"speed_cycling": 6.0}   # 100 m a minute, so 200 m is 2 minutes


def test_a_cycle_track_of_its_own_beside_a_busy_road_is_used(scenario):
    # 10 m north of the road, joined to every street it crosses.
    track = [xy(0, BUSY_ROW + 0.1), xy(N - 1, BUSY_ROW + 0.1)]
    built = scenario([(track, {"highway": "cycleway"})], busy_row=BUSY_ROW)

    before, after = built.minutes(A, B, "bicycle", **SLOW)

    assert 7 <= before <= 9     # round the busy road
    assert 5 <= after <= 7      # beside it, on the track
    assert after < before


@pytest.mark.xfail(strict=True, reason=(
    "R5's traffic stress rules read cycleway=lane (and opposite lanes) but not "
    "cycleway=track: the busy road stays at LTS 4. The tag is in the PBF."))
def test_a_cycle_track_tagged_on_a_busy_road_opens_it_to_cyclists(scenario):
    whole_row = [xy(0, BUSY_ROW), xy(N - 1, BUSY_ROW)]
    built = scenario([(whole_row, {"osm_id": row_way(BUSY_ROW), "cycleway": "track"})],
                     busy_row=BUSY_ROW)

    before, after = built.minutes(A, B, "bicycle", **SLOW)

    assert after < before
