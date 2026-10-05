"""`networkforge build` end to end on the synthetic grid (download faked)."""

import json

import geopandas as gpd
import osmium
import pandas as pd
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


def test_done_event_counts_match_the_geopackage(fake_osm, tmp_path, capsys):
    """
    Edges are counted per street, as GeoPackage rows: OSMnx holds a
    two-way street as two edges, which made the counts nearly double.
    """
    BBOX.to_file(tmp_path / "extent.gpkg")
    row_1 = [(500_100.0, 6_200_100.0), (500_200.0, 6_200_100.0)]  # Row 1 Street, nodes 7-8
    gpd.GeoDataFrame(
        {"osm_id": [None, 2, 2], "highway": ["primary", None, None],
         "oneway": [None, "yes", None], "remove": [None, None, "yes"]},
        geometry=[LineString(ROAD_ACROSS), LineString(row_1),
                  LineString([(500_300.0, 6_200_100.0), (500_400.0, 6_200_100.0)])],
        crs=UTM,
    ).to_file(tmp_path / "custom.gpkg")

    code = main(["build", "--extent", str(tmp_path / "extent.gpkg"),
                 "--custom", str(tmp_path / "custom.gpkg"),
                 "--gpkg", str(tmp_path / "after.gpkg"),
                 "--baseline-gpkg", str(tmp_path / "before.gpkg"), "--json"])
    done = events(capsys)[-1]
    after = gpd.read_file(tmp_path / "after.gpkg", layer="edges")

    assert code == EXIT_OK
    assert done["edges"] == len(after)
    assert done["custom_edges"] == (after["custom"] == "yes").sum() > 0
    assert done["modified_edges"] == (after["modified"] == "yes").sum() == 1
    assert done["removed_edges"] == 1


def test_oneway_is_osm_text_in_every_geopackage_row(fake_osm, tmp_path, capsys):
    """Not a mix of booleans and text when new and changed lines join OSM streets."""
    BBOX.to_file(tmp_path / "extent.gpkg")
    gpd.GeoDataFrame(
        {"osm_id": [None, 2], "highway": ["primary", None], "oneway": ["-1", "yes"]},
        geometry=[LineString(ROAD_ACROSS),
                  LineString([(500_100.0, 6_200_100.0), (500_200.0, 6_200_100.0)])],
        crs=UTM,
    ).to_file(tmp_path / "custom.gpkg")
    main(["build", "--extent", str(tmp_path / "extent.gpkg"),
          "--custom", str(tmp_path / "custom.gpkg"), "--gpkg", str(tmp_path / "after.gpkg"),
          "--baseline-gpkg", str(tmp_path / "before.gpkg")])

    for name in ("before", "after"):
        layer = gpd.read_file(tmp_path / f"{name}.gpkg", layer="edges")
        assert not pd.api.types.is_bool_dtype(layer["oneway"])
        assert set(layer["oneway"]) <= {"yes", "no", "-1"}, set(layer["oneway"])
    after = gpd.read_file(tmp_path / "after.gpkg", layer="edges")
    assert set(after.loc[after.custom == "yes", "oneway"]) == {"-1"}
    assert set(after.loc[after.modified == "yes", "oneway"]) == {"yes"}
    assert "no" in set(after.loc[after.custom != "yes", "oneway"])
