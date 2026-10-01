"""
build_network options and failure handling, on the synthetic grid:
presets and overwrite, progress reporting, logging, and clear errors
when the download or the data can't be used.
"""

import logging

import geopandas as gpd
import osmnx as ox
import pytest
import requests
from osmnx._errors import InsufficientResponseError
from shapely.geometry import LineString

from networkforge import build_network
from networkforge.errors import (
    InputError,
    NetworkIntegrityError,
    NoIntersectionError,
    OSMDownloadError,
)
from networkforge.network import TOTAL_STEPS
from networkforge.validation import (
    assert_all_custom_edges_are_connected,
    disconnected_custom_edges,
)
from tests.integration.grid import BBOX, DIAGONAL, ROAD_ACROSS, UTM


def custom_tags(result):
    return result.edges[result.edges["custom"] == "yes"]


def test_preset_applies_to_every_feature(build):
    result = build([(ROAD_ACROSS, {}), (DIAGONAL, {})], preset="cycleway")
    assert set(custom_tags(result)["highway"]) == {"cycleway"}


def test_network_tags_add_to_a_preset(build):
    result = build([(ROAD_ACROSS, {})], preset="primary_road",
                   network_tags={"maxspeed": "40 mph"})
    tags = custom_tags(result)
    assert set(tags["highway"]) == {"primary"}
    assert set(tags["maxspeed"]) == {"40 mph"}


def test_feature_attributes_beat_the_preset(build):
    result = build([(ROAD_ACROSS, {}), (DIAGONAL, {"highway": "footway"})],
                   preset="primary_road")
    assert set(custom_tags(result)["highway"]) == {"primary", "footway"}


def test_overwrite_makes_the_preset_win(build):
    result = build([(ROAD_ACROSS, {"highway": "primary", "name": "Bypass"})],
                   preset="cycleway", overwrite_tags=True)
    tags = custom_tags(result)
    assert set(tags["highway"]) == {"cycleway"}
    assert set(tags["name"]) == {"Bypass"}


def test_progress_callback_reports_every_step(build):
    calls = []
    build([(ROAD_ACROSS, {"highway": "primary"})],
          progress=lambda step, total, text: calls.append((step, total)))
    assert calls == [(step, TOTAL_STEPS) for step in range(1, TOTAL_STEPS + 1)]


def test_steps_are_logged_not_printed(build, caplog, capsys):
    with caplog.at_level(logging.INFO, logger="networkforge"):
        build([(ROAD_ACROSS, {"highway": "primary"})])

    assert f"[{TOTAL_STEPS}/{TOTAL_STEPS}]" in caplog.text
    assert "Network built" in caplog.text
    assert capsys.readouterr().out == ""


def test_line_far_from_every_street_is_a_clear_error(build):
    inside_a_block = [(500_030.0, 6_200_030.0), (500_060.0, 6_200_060.0)]
    with pytest.raises(NoIntersectionError, match="touch"):
        build([(inside_a_block, {"highway": "primary"})])


def test_line_away_from_the_network_only_warns(build, caplog):
    # One line joins the grid, the other sits inside a block: the build
    # succeeds, keeps both, and names the unreachable feature.
    inside_a_block = [(500_030.0, 6_200_030.0), (500_060.0, 6_200_060.0)]
    with caplog.at_level(logging.WARNING, logger="networkforge"):
        result = build([(inside_a_block, {"highway": "primary"}),
                        (ROAD_ACROSS, {"highway": "primary"})])

    warnings = [r for r in caplog.records if "don't connect" in r.getMessage()]
    assert [r.features for r in warnings] == [[0]]

    stranded = result.edges[disconnected_custom_edges(result.edges)]
    assert len(stranded) == 1
    assert stranded.geometry.iloc[0].equals(LineString(inside_a_block))
    with pytest.raises(NetworkIntegrityError, match="isolated"):
        assert_all_custom_edges_are_connected(result.edges)


def test_connected_lines_give_no_disconnected_warning(build, caplog):
    with caplog.at_level(logging.WARNING, logger="networkforge"):
        result = build([(ROAD_ACROSS, {"highway": "primary"})])

    assert "don't connect" not in caplog.text
    assert_all_custom_edges_are_connected(result.edges)


def test_bad_snap_tolerance_rejected(build):
    with pytest.raises(InputError, match="snap_tolerance"):
        build([(ROAD_ACROSS, {"highway": "primary"})], snap_tolerance=0)


@pytest.mark.parametrize("failure, message", [
    (requests.ConnectionError("no route to host"), "internet connection"),
    (InsufficientResponseError("empty"), "no 'all' ways"),
])
def test_download_failures_become_osm_download_errors(monkeypatch, failure, message):
    def failing_download(*args, **kwargs):
        raise failure

    monkeypatch.setattr(ox, "graph_from_bbox", failing_download)
    custom = gpd.GeoDataFrame({"highway": ["primary"]},
                              geometry=[LineString(ROAD_ACROSS)], crs=UTM)

    with pytest.raises(OSMDownloadError, match=message):
        build_network(BBOX, custom)
