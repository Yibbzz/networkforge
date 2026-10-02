"""`networkforge build` end to end on the synthetic grid (download faked)."""

import json

import geopandas as gpd
import osmium
import pyogrio
import pytest
from shapely.geometry import LineString

from networkforge.cli import EXIT_DOWNLOAD, EXIT_OK, main
from networkforge.network import TOTAL_STEPS
from tests.integration.grid import BBOX, DIAGONAL, ROAD_ACROSS, UTM


@pytest.fixture
def files(tmp_path):
    extent = tmp_path / "extent.gpkg"
    BBOX.to_file(extent)
    custom = tmp_path / "custom.gpkg"
    gpd.GeoDataFrame({"highway": ["primary", "cycleway"]},
                     geometry=[LineString(ROAD_ACROSS), LineString(DIAGONAL)],
                     crs=UTM).to_file(custom)
    return extent, custom, tmp_path


def events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_build_writes_every_output_and_reports_json(fake_osm, files, capsys):
    extent, custom, out = files
    code = main(["build", "--extent", str(extent), "--custom", str(custom),
                 "--tag", "maxspeed=30 mph", "--out", str(out / "net.osm.pbf"),
                 "--baseline-out", str(out / "base.osm"), "--gpkg", str(out / "net.gpkg"),
                 "--json"])

    assert code == EXIT_OK
    log = events(capsys)
    steps = [e["step"] for e in log if e["event"] == "progress"]
    assert steps == list(range(1, TOTAL_STEPS + 1))

    done = log[-1]
    assert done["event"] == "done"
    assert set(done["outputs"]) == {"osm", "baseline_osm", "gpkg"}
    assert done["custom_edges"] > 0

    assert sorted(pyogrio.list_layers(out / "net.gpkg")[:, 0]) == ["edges", "nodes"]
    custom_ways = [dict(w.tags) for w in osmium.FileProcessor(str(out / "net.osm.pbf"),
                                                              osmium.osm.WAY)
                   if w.tags.get("nf:custom") == "yes"]
    assert {w["highway"] for w in custom_ways} == {"primary", "cycleway"}
    assert all(w["maxspeed"] == "30 mph" for w in custom_ways)


def test_build_prints_a_readable_summary(fake_osm, files, capsys):
    extent, custom, out = files
    code = main(["build", "--extent", str(extent), "--custom", str(custom),
                 "--out", str(out / "net.osm")])
    assert code == EXIT_OK
    assert capsys.readouterr().out.startswith("Done: ")


def test_download_failure_exit_code(monkeypatch, files, capsys):
    import requests

    from networkforge import osm

    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(osm, "_download_elements", offline)
    extent, custom, out = files
    code = main(["build", "--extent", str(extent), "--custom", str(custom),
                 "--out", str(out / "net.osm"), "--json"])

    assert code == EXIT_DOWNLOAD
    assert events(capsys)[-1]["type"] == "OSMDownloadError"


def test_build_writes_before_and_after_geopackages(fake_osm, files, capsys):
    extent, custom, out = files
    code = main(["build", "--extent", str(extent), "--custom", str(custom),
                 "--tag", "maxspeed=30 mph", "--gpkg", str(out / "after.gpkg"),
                 "--baseline-gpkg", str(out / "before.gpkg"), "--json"])
    assert code == EXIT_OK
    assert set(events(capsys)[-1]["outputs"]) == {"gpkg", "baseline_gpkg"}

    before = gpd.read_file(out / "before.gpkg", layer="edges")
    after = gpd.read_file(out / "after.gpkg", layer="edges")
    for layer in (before, after):
        assert {"car", "bike", "walk", "speed_kph", "car_direction"} <= set(layer.columns)
    assert "custom" not in before.columns or not (before["custom"] == "yes").any()
    assert (after["custom"] == "yes").sum() > 0
    assert len(after) > len(before)


def test_disconnected_feature_is_a_warning_event_not_a_failure(fake_osm, tmp_path, capsys):
    extent = tmp_path / "extent.gpkg"
    BBOX.to_file(extent)
    custom = tmp_path / "custom.gpkg"
    inside_a_block = [(500_030.0, 6_200_030.0), (500_060.0, 6_200_060.0)]
    gpd.GeoDataFrame({"ref": ["joined", "island"], "highway": ["primary", "primary"]},
                     geometry=[LineString(ROAD_ACROSS), LineString(inside_a_block)],
                     crs=UTM).to_file(custom)

    code = main(["build", "--extent", str(extent), "--custom", str(custom),
                 "--id-field", "ref", "--out", str(tmp_path / "net.osm"), "--json"])

    assert code == EXIT_OK
    log = events(capsys)
    warnings = [e for e in log if e["event"] == "warning"]
    assert [w["features"] for w in warnings if "don't connect" in w["message"]] == [["island"]]
    assert log[-1]["event"] == "done"
