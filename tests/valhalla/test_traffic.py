"""
Traffic (`networkforge traffic`): time bands by road class, routed on the
.tar it writes, as the Network Analyst plugin does after "From Tar".

The grid's streets are residential; ROAD_ACROSS adds a primary road
between rows 1 and 2. A trip along it is 300 m; the nearest way round,
up to row 1 or row 2 and back, is 400 m on streets of the same speed. Slowing the
primary road to a fifth at peak time sends a timed peak trip round, and
nothing else changes.
"""

import contextlib
import io
import json

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from networkforge.cli import EXIT_OK, main
from tests.integration.grid import ROAD_ACROSS, ROAD_TAGS, UTM
from tests.valhalla.conftest import at, xy
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


# ---------------------------------------------------------------- single streets

ALONG_ROW_3 = (at(0.5, 3), at(3.5, 3))
ROW_3_BACK = (at(3.5, 3), at(0.5, 3))
ROW_3_MIDDLE = [xy(1, 3), xy(3, 3)]       # drawn west to east, junction to junction
PEAK = {"days": "Mo-Fr", "time": "07:00-09:30"}


def streets_tar(pbf, folder, rows, settings=None) -> tuple[Router, list[dict]]:
    """
    A Router on a tar made with single-street rows [(coords or None,
    fields)], and the command's JSON events.
    """
    geometries = [LineString(coords) if coords else None for coords, _ in rows]
    gpd.GeoDataFrame([{"fid_": i, **fields} for i, (_, fields) in enumerate(rows, start=1)],
                     geometry=geometries, crs=UTM).to_file(folder / "streets.gpkg")
    arguments = ["traffic", "--pbf", str(pbf), "--streets", str(folder / "streets.gpkg"),
                 "--id-field", "fid_", "--out", str(folder / "streets.tar"), "--json"]
    if settings:
        (folder / "traffic.toml").write_text(settings)
        arguments += ["--settings", str(folder / "traffic.toml")]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(arguments) == EXIT_OK
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    return Router.from_tar(folder / "streets.tar", pbf, folder / "tar"), events


def goes_round_row_3(route) -> bool:
    return bool({"Row 2 Street", "Row 4 Street"} & set(route.names))


def test_a_street_by_its_osm_way_id(scenario, tmp_path):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, events = streets_tar(built.after.pbf, tmp_path,
                                  [(None, {"osm_id": built.custom_ways[0], **PEAK,
                                           "factor": 0.2})])
    peak = traffic.route(*ALONG_THE_NEW_ROAD, "auto", depart_at=MONDAY_PEAK)
    assert {"Row 1 Street", "Row 2 Street"} & set(peak.names)
    assert events[-1]["single_streets"] == 1


def test_a_line_drawn_along_part_of_a_street(scenario, tmp_path):
    """Row 3 Street is one long way; only the stretch drawn is slowed."""
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, events = streets_tar(built.after.pbf, tmp_path, [(ROW_3_MIDDLE, {**PEAK,
                                                                              "factor": 0.2})])
    normal = built.after.route(*ALONG_ROW_3, "auto")
    peak = traffic.route(*ALONG_ROW_3, "auto", depart_at=MONDAY_PEAK)
    assert not goes_round_row_3(normal)
    assert goes_round_row_3(peak)
    # The two blocks drawn, both directions: four segments.
    assert events[-1]["single_segments"] == 4
    # A trip on the rest of Row 3 Street (west of column 1) is not slowed.
    west = (at(0, 3.5), at(0.5, 3))
    assert (traffic.route(*west, "auto", depart_at=MONDAY_PEAK).time_s
            == pytest.approx(built.after.route(*west, "auto").time_s, rel=PACKING))


def test_a_single_street_overrides_its_road_class(scenario, tmp_path):
    """Primary roads are slowed at peak, but this one is said to flow freely."""
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, _ = streets_tar(built.after.pbf, tmp_path,
                             [(None, {"osmid": built.custom_ways[0], **PEAK, "factor": 1.0})],
                             settings=PEAK_ON_PRIMARY)
    normal = built.after.route(*ALONG_THE_NEW_ROAD, "auto")
    peak = traffic.route(*ALONG_THE_NEW_ROAD, "auto", depart_at=MONDAY_PEAK)
    assert peak.names == normal.names
    assert peak.time_s == pytest.approx(normal.time_s, rel=PACKING)


def test_one_direction_only(scenario, tmp_path):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, _ = streets_tar(built.after.pbf, tmp_path,
                             [(ROW_3_MIDDLE, {**PEAK, "factor": 0.2, "direction": "forward"})])
    assert goes_round_row_3(traffic.route(*ALONG_ROW_3, "auto", depart_at=MONDAY_PEAK))
    back = traffic.route(*ROW_3_BACK, "auto", depart_at=MONDAY_PEAK)
    assert not goes_round_row_3(back)


def test_a_line_on_no_street_is_named(scenario, tmp_path):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    far_away = [xy(1.5, 7), xy(3.5, 7)]          # two blocks north of the grid
    _, events = streets_tar(built.after.pbf, tmp_path,
                            [(ROW_3_MIDDLE, {**PEAK, "factor": 0.5}),
                             (far_away, {**PEAK, "factor": 0.5})])
    warnings = [e for e in events if e["event"] == "warning"]
    assert any(w.get("features") == [2] for w in warnings)
    assert events[-1]["unmatched"] == [2]
    assert events[-1]["single_streets"] == 1


def test_a_line_with_a_way_id_only_sets_that_way(scenario, tmp_path):
    """
    As a row copied from the network layer: its line and its `osmid`.
    Row 3 Street is two ways either side of node 18 (the turn
    restriction's via node), and the line runs along both; only the
    named way's block is slowed.
    """
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    west_of_18 = next(way for way, (refs, tags) in built.after.ways.items()
                      if tags.get("name") == "Row 3 Street" and refs[-1] == 18)
    _, events = streets_tar(built.after.pbf, tmp_path,
                            [(ROW_3_MIDDLE, {"osmid": west_of_18, **PEAK, "factor": 0.2})])
    assert events[-1]["single_segments"] == 2          # one block, both directions


# ---------------------------------------------------------------- measured speeds

def table_tar(pbf, folder, rows, settings=None) -> tuple[Router, list[dict]]:
    """A Router on a tar made from a speed table (a CSV of rows {osm_id, ...}), and the events."""
    import csv

    with (folder / "speeds.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=sorted({k for row in rows for k in row}))
        writer.writeheader()
        writer.writerows(rows)
    arguments = ["traffic", "--pbf", str(pbf), "--speed-table", str(folder / "speeds.csv"),
                 "--out", str(folder / "table.tar"), "--json"]
    if settings:
        (folder / "traffic.toml").write_text(settings)
        arguments += ["--settings", str(folder / "traffic.toml")]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(arguments) == EXIT_OK
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    return Router.from_tar(folder / "table.tar", pbf, folder / "tar"), events


def test_a_measured_speed_on_a_drawn_street(scenario, tmp_path):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, _ = streets_tar(built.after.pbf, tmp_path,
                             [(ROW_3_MIDDLE, {**PEAK, "speed_kph": 4})])
    assert goes_round_row_3(traffic.route(*ALONG_ROW_3, "auto", depart_at=MONDAY_PEAK))
    night = traffic.route(*ALONG_ROW_3, "auto", depart_at=MONDAY_NIGHT)
    assert not goes_round_row_3(night)


def test_a_speed_table_by_way_id(scenario, tmp_path):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, events = table_tar(built.after.pbf, tmp_path, [
        {"osm_id": built.custom_ways[0], "days": "Mo-Fr", "time": "07:00-09:30",
         "speed_kph": 5}])
    normal = built.after.route(*ALONG_THE_NEW_ROAD, "auto")
    peak = traffic.route(*ALONG_THE_NEW_ROAD, "auto", depart_at=MONDAY_PEAK)
    assert {"Row 1 Street", "Row 2 Street"} & set(peak.names)
    assert peak.time_s > normal.time_s
    assert events[-1]["table_rows"] == 1 and events[-1]["unmatched_rows"] == []


def test_backward_is_the_other_way_from_the_line(scenario, tmp_path):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, _ = streets_tar(built.after.pbf, tmp_path,
                             [(ROW_3_MIDDLE, {**PEAK, "factor": 0.2, "direction": "backward"})])
    # Drawn west to east: backward slows the trip west only.
    assert not goes_round_row_3(traffic.route(*ALONG_ROW_3, "auto", depart_at=MONDAY_PEAK))
    assert goes_round_row_3(traffic.route(*ROW_3_BACK, "auto", depart_at=MONDAY_PEAK))


def test_a_table_row_naming_no_way_is_reported_by_row(scenario, tmp_path):
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    _, events = table_tar(built.after.pbf, tmp_path, [
        {"osm_id": built.custom_ways[0], "time": "07:00-09:30", "factor": 0.5},
        {"osm_id": 987654321, "time": "07:00-09:30", "factor": 0.5}])
    warnings = [e for e in events if e["event"] == "warning"]
    assert any(w.get("rows") == [2] and "features" not in w for w in warnings)
    assert events[-1]["unmatched_rows"] == [2] and events[-1]["table_rows"] == 1


def test_a_way_id_backward_is_against_the_ways_own_direction(scenario, tmp_path):
    """The new road's way runs west to east, as ROAD_ACROSS is drawn."""
    built = scenario([(ROAD_ACROSS, ROAD_TAGS)])
    traffic, _ = table_tar(built.after.pbf, tmp_path, [
        {"osm_id": built.custom_ways[0], "time": "07:00-09:30", "speed_kph": 5,
         "direction": "backward"}])
    east = traffic.route(*ALONG_THE_NEW_ROAD, "auto", depart_at=MONDAY_PEAK)
    west = traffic.route(*reversed(ALONG_THE_NEW_ROAD), "auto", depart_at=MONDAY_PEAK)
    assert not {"Row 1 Street", "Row 2 Street"} & set(east.names)
    assert {"Row 1 Street", "Row 2 Street"} & set(west.names)
