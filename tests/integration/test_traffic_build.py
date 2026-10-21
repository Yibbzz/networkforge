"""
The traffic build end to end on a tiny network, offline and quick (no
routing: tests/valhalla/test_traffic.py proves the routes). A 2 km main
road (way 100, drawn west to east) with a slower detour (way 200).
"""

import json

import geopandas as gpd
import osmium
import pandas as pd
import pytest
from shapely.geometry import LineString

pytest.importorskip("valhalla", reason="pyvalhalla is not installed (uv sync installs it)")

from networkforge.cli import EXIT_INPUT, EXIT_OK, main  # noqa: E402
from networkforge.errors import InputError  # noqa: E402
from networkforge.traffic import (  # noqa: E402
    add_traffic,
    parse_bands,
    speed_table,
    street_bands,
)

LAT, LON = 51.45, -0.95


def lon(metres: float) -> float:
    return LON + metres / 69500.0


@pytest.fixture(scope="module")
def pbf(tmp_path_factory):
    path = tmp_path_factory.mktemp("traffic") / "road.osm.pbf"
    writer = osmium.SimpleWriter(str(path))
    for node, (x, y) in {1: (0, 0), 2: (2000, 0), 3: (0, 300), 4: (2000, 300)}.items():
        writer.add_node(osmium.osm.mutable.Node(
            id=node, location=(lon(x), LAT + y / 111200.0), tags={}, version=1))
    writer.add_way(osmium.osm.mutable.Way(id=100, nodes=[1, 2], version=1, tags={
        "highway": "primary", "maxspeed": "50", "name": "Main"}))
    writer.add_way(osmium.osm.mutable.Way(id=200, nodes=[1, 3, 4, 2], version=1, tags={
        "highway": "residential", "maxspeed": "30", "name": "Detour"}))
    writer.close()
    return path


PEAK = {"days": "Mo-Fr", "time": "07:00-09:30"}
MAIN_ROAD = [(lon(200), LAT), (lon(1800), LAT)]


def test_bands_streets_and_a_table_together(pbf, tmp_path):
    bands = parse_bands([{**PEAK, "roads": ["residential"], "factor": 0.5}])
    streets = street_bands(gpd.GeoDataFrame(
        {**PEAK, "speed_kph": [10], "direction": ["backward"]},
        geometry=[LineString(MAIN_ROAD)], crs="EPSG:4326", index=[7]))
    table = speed_table(pd.DataFrame(
        {"osm_id": ["100", "999"], **{k: [v, v] for k, v in PEAK.items()},
         "factor": ["0.5", "0.5"], "direction": ["forward", "both"]}, index=[1, 2]))
    result = add_traffic(pbf, bands, tmp_path / "out.tar", streets=streets + table)

    assert (tmp_path / "out.tar").stat().st_size > 0
    assert result.by_road == {"residential": 1}
    assert result.single_streets == 1               # the line, backward: one direction
    assert result.table_rows == 1                   # way 100 forward; 999 isn't there
    assert result.unmatched_rows == [2]
    assert result.single_segments == 2             # main road: one edge each way
    assert result.ways == 2


def test_a_line_on_no_street_gets_no_traffic(pbf, tmp_path):
    far = [(lon(500), LAT + 0.05), (lon(900), LAT + 0.05)]
    streets = street_bands(gpd.GeoDataFrame({**PEAK, "factor": [0.5]},
                                            geometry=[LineString(far)], crs="EPSG:4326",
                                            index=[3]))
    result = add_traffic(pbf, [], tmp_path / "out.tar", streets=streets)
    assert result.unmatched == [3] and result.single_streets == 0
    assert result.segments == 0


@pytest.mark.parametrize(("out", "words"), [("out.osm", "must be a .tar"),
                                             ("missing/out.tar", "folder")])
def test_bad_outputs(pbf, tmp_path, out, words):
    bands = parse_bands([{**PEAK, "roads": "all", "factor": 0.5}])
    with pytest.raises(InputError, match=words):
        add_traffic(pbf, bands, tmp_path / out)


def test_nothing_to_add_and_no_file(pbf, tmp_path):
    with pytest.raises(InputError, match="No traffic"):
        add_traffic(pbf, [], tmp_path / "out.tar")
    bands = parse_bands([{**PEAK, "roads": "all", "factor": 0.5}])
    with pytest.raises(InputError, match="doesn't exist"):
        add_traffic(tmp_path / "none.osm.pbf", bands, tmp_path / "out.tar")


def json_events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_the_command_with_a_speed_table(pbf, tmp_path, capsys):
    (tmp_path / "speeds.csv").write_text(
        "osm_id,days,time,speed_kph,direction,ref\n"
        "100,Mo-Fr,07:00-09:30,12,both,a\n"
        "555,,07:00-09:30,12,,b\n")
    assert main(["traffic", "--pbf", str(pbf), "--speed-table", str(tmp_path / "speeds.csv"),
                 "--speed-table-id-field", "ref", "--out", str(tmp_path / "t.tar"),
                 "--json"]) == EXIT_OK
    events = json_events(capsys)
    warning = next(e for e in events if e["event"] == "warning")
    assert warning["rows"] == ["b"] and "features" not in warning
    done = events[-1]
    assert (done["table_rows"], done["unmatched_rows"]) == (1, ["b"])


@pytest.mark.parametrize(("arguments", "words"), [
    ([], "No traffic to add"),
    (["--speed-table", "missing.csv"], "doesn't exist"),
    (["--speed-table", "speeds.csv", "--speed-table-id-field", "nope"], "isn't a column"),
])
def test_the_command_refuses(pbf, tmp_path, capsys, monkeypatch, arguments, words):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "speeds.csv").write_text("osm_id,time,factor\n100,07:00-09:00,0.5\n")
    code = main(["traffic", "--pbf", str(pbf), "--out", "t.tar", "--json", *arguments])
    assert code == EXIT_INPUT
    assert words in json_events(capsys)[-1]["message"]


def test_a_short_deep_band_is_lifted_off_the_floor():
    """Packed as given, 100 down to 8 km/h for 90 minutes unpacks below 0 km/h."""
    from valhalla.baldr.utils import compress_speed_buckets, decompress_speed_bucket

    from networkforge.traffic import BUCKETS_PER_DAY, LIFT_BELOW, _packable, week_speeds

    band = parse_bands([{"days": "Mo-Fr", "time": "07:30-09:00", "roads": "all",
                         "speed_kph": 8}])[0]
    target = week_speeds([band], 100.0)
    packed, off = _packable(target, 100.0, compress_speed_buckets, decompress_speed_bucket)
    monday = [decompress_speed_bucket(packed, BUCKETS_PER_DAY + i)
              for i in range(90, 108)]                      # 07:30-09:00
    assert min(monday) >= LIFT_BELOW * 8 - 0.5
    assert off                                             # and it's reported as approximate
    plain = compress_speed_buckets(target)
    assert min(decompress_speed_bucket(plain, BUCKETS_PER_DAY + i) for i in range(90, 108)) < 0


def test_an_approximated_band_is_warned_about(pbf, tmp_path, caplog):
    table = speed_table(pd.DataFrame({"osm_id": ["100"], "time": ["08:00-09:00"],
                                      "speed_kph": ["4"]}, index=[1]))
    add_traffic(pbf, [], tmp_path / "out.tar", streets=table)
    assert "can only approximate" in caplog.text


def test_ordinary_bands_are_not_warned_about(pbf, tmp_path, caplog):
    bands = parse_bands([{**PEAK, "roads": "all", "factor": 0.6}])
    add_traffic(pbf, bands, tmp_path / "out.tar")
    assert "can only approximate" not in caplog.text
