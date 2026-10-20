"""
Traffic (`networkforge traffic`): time bands by road class, routed on the
.tar it writes, as the Network Analyst plugin does after "From Tar".

The grid's streets are residential; ROAD_ACROSS adds a primary road
between rows 1 and 2. A trip along it is 300 m; the nearest way round,
up to row 1 or row 2 and back, is 400 m on streets of the same speed. Slowing the
primary road to a fifth at peak time sends a timed peak trip round, and
nothing else changes.
"""

import json

import pytest

from networkforge.cli import EXIT_OK, main
from tests.integration.grid import ROAD_ACROSS, ROAD_TAGS
from tests.valhalla.conftest import at
from tests.valhalla.harness import Router

MONDAY_PEAK = "2026-10-19T08:00"
MONDAY_NIGHT = "2026-10-19T03:00"
SUNDAY_MORNING = "2026-10-18T08:00"

# Valhalla packs a week of speeds into 200 numbers, which blurs them a
# little: away from a band's edges a trip can be a few percent off.
PACKING = 0.08

ALONG_THE_NEW_ROAD = (at(0.5, 1.5), at(3.5, 1.5))
ON_RESIDENTIAL_STREETS = (at(0.5, 3), at(3.5, 3))

PEAK_ON_PRIMARY = """
[[band]]
name = "Morning peak"
days = "Mo-Fr"
time = "07:00-09:30"
roads = ["primary"]
factor = 0.2
"""


def add_traffic(pbf, folder, settings) -> Router:
    (folder / "traffic.toml").write_text(settings)
    tar = folder / "traffic.tar"
    assert main(["traffic", "--pbf", str(pbf), "--settings", str(folder / "traffic.toml"),
                 "--out", str(tar), "--json"]) == EXIT_OK
    return Router.from_tar(tar, pbf, folder / "tar")


@pytest.fixture(scope="module")
def roads(scenario, tmp_path_factory):
    """(the build, a Router on its after network's traffic tar)."""
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    folder = tmp_path_factory.mktemp("traffic")
    return built, add_traffic(built.after.pbf, folder, PEAK_ON_PRIMARY)


def test_a_timed_peak_trip_avoids_the_slowed_road(roads):
    built, traffic = roads
    normal = built.after.route(*ALONG_THE_NEW_ROAD, "auto")
    peak = traffic.route(*ALONG_THE_NEW_ROAD, "auto", depart_at=MONDAY_PEAK)
    # The trip starts and ends on the new road; at peak it leaves it for
    # the residential streets of row 1 or 2 in between.
    assert not {"Row 1 Street", "Row 2 Street"} & set(normal.names)
    assert {"Row 1 Street", "Row 2 Street"} & set(peak.names)
    assert peak.length_m == pytest.approx(normal.length_m + 100, abs=5)
    assert peak.time_s > normal.time_s


@pytest.mark.parametrize("when", [MONDAY_NIGHT, SUNDAY_MORNING])
def test_outside_the_band_the_trip_is_as_without_traffic(roads, when):
    built, traffic = roads
    normal = built.after.route(*ALONG_THE_NEW_ROAD, "auto")
    timed = traffic.route(*ALONG_THE_NEW_ROAD, "auto", depart_at=when)
    assert timed.way_ids == normal.way_ids
    assert timed.time_s == pytest.approx(normal.time_s, rel=PACKING)


def test_a_trip_without_a_time_ignores_traffic(roads):
    """Requests without a time (all of Network Analyst's tools) see the normal network."""
    built, traffic = roads
    normal = built.after.route(*ALONG_THE_NEW_ROAD, "auto")
    untimed = traffic.route(*ALONG_THE_NEW_ROAD, "auto")
    assert untimed.way_ids == normal.way_ids
    assert untimed.time_s == normal.time_s


def test_streets_outside_the_bands_keep_their_speed_at_peak(roads):
    built, traffic = roads
    normal = built.after.route(*ON_RESIDENTIAL_STREETS, "auto")
    peak = traffic.route(*ON_RESIDENTIAL_STREETS, "auto", depart_at=MONDAY_PEAK)
    assert peak.way_ids == normal.way_ids
    assert peak.time_s == pytest.approx(normal.time_s, rel=PACKING)


def test_walking_is_not_slowed(roads):
    """Traffic is for vehicles: Valhalla's pedestrian costing ignores it."""
    built, traffic = roads
    normal = built.after.route(*ALONG_THE_NEW_ROAD, "pedestrian")
    peak = traffic.route(*ALONG_THE_NEW_ROAD, "pedestrian", depart_at=MONDAY_PEAK)
    assert peak.way_ids == normal.way_ids
    assert peak.time_s == pytest.approx(normal.time_s, rel=0.01)


def test_the_before_network_gets_the_same_settings(scenario, tmp_path_factory):
    """The grid's own streets are residential: a band on them slows the before network too."""
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    folder = tmp_path_factory.mktemp("traffic-before")
    traffic = add_traffic(built.before.pbf, folder,
                          PEAK_ON_PRIMARY.replace('["primary"]', '["residential"]'))
    normal = built.before.route(*ON_RESIDENTIAL_STREETS, "auto")
    peak = traffic.route(*ON_RESIDENTIAL_STREETS, "auto", depart_at=MONDAY_PEAK)
    assert peak.time_s > 2 * normal.time_s


def test_done_event_counts_the_streets(scenario, tmp_path, capsys):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    (tmp_path / "traffic.toml").write_text(PEAK_ON_PRIMARY)
    capsys.readouterr()
    assert main(["traffic", "--pbf", str(built.after.pbf),
                 "--settings", str(tmp_path / "traffic.toml"),
                 "--out", str(tmp_path / "after.tar"), "--json"]) == EXIT_OK
    done = [json.loads(line) for line in capsys.readouterr().out.splitlines()][-1]
    assert done["event"] == "done"
    assert done["outputs"] == {"tar": str(tmp_path / "after.tar")}
    assert done["by_road"] == {"primary": 1}           # the one new road, one way
    assert done["streets"] == 1
    assert done["segments"] >= 2                        # both directions, cut at each junction
    assert done["bands"] == ["Morning peak"]
    assert done["streets_not_found"] == []
