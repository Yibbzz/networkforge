"""
Reading the existing network from a local OSM file (osm_source=...)
instead of Overpass, and the Overpass area limit.
"""

import logging

import geopandas as gpd
import osmium
import osmnx as ox
import pytest
from shapely.geometry import LineString, box

from networkforge import write_osm
from networkforge.errors import InputError
from networkforge.osm import _extract_area
from tests.helpers import ROUTING_MODES, build_and_export, custom_pairs
from tests.integration.grid import (
    BBOX,
    BOLLARD_NODE,
    DIAGONAL,
    GRID_NODES,
    ROAD_ACROSS,
    ROAD_TAGS,
    UTM,
    X0,
    Y0,
    all_pair_costs,
    node_id,
    synthetic_graph,
)


@pytest.fixture(scope="module")
def grid_pbf(tmp_path_factory):
    """The synthetic grid written as a .osm.pbf extract."""
    nodes, edges = ox.graph_to_gdfs(synthetic_graph())
    path = tmp_path_factory.mktemp("extract") / "grid.osm.pbf"
    write_osm(nodes, edges.reset_index(), path)
    return path


def custom(*features):
    return gpd.GeoDataFrame([tags for _, tags in features],
                            geometry=[LineString(coords) for coords, _ in features], crs=UTM)


FEATURES = [(ROAD_ACROSS, ROAD_TAGS), (DIAGONAL, {"highway": "cycleway"})]


@pytest.mark.parametrize("mode", ROUTING_MODES)
def test_file_source_routes_like_the_download(build, grid_pbf, tmp_path, mode):
    downloaded = build(FEATURES)
    (tmp_path / "file").mkdir()
    from_file = build_and_export(BBOX, custom(*FEATURES), tmp_path / "file", osm_source=grid_pbf)

    # OSM files store coordinates to 7 decimals (~1 cm); the synthetic grid
    # isn't rounded, so the file's nodes sit a few millimetres away.
    for which in ("baseline", "custom"):
        expected = all_pair_costs(downloaded.graph(which, mode), mode)
        assert all_pair_costs(from_file.graph(which, mode), mode) == pytest.approx(
            expected, abs=0.05)
    assert bool(custom_pairs(from_file.graph("custom", mode))) == bool(
        custom_pairs(downloaded.graph("custom", mode)))


def test_file_source_keeps_node_tags(grid_pbf, tmp_path):
    result = build_and_export(BBOX, custom(*FEATURES), tmp_path, osm_source=grid_pbf)
    assert result.osm_nodes.loc[BOLLARD_NODE, "barrier"] == "bollard"


def test_file_source_does_not_call_overpass(fake_osm, grid_pbf, tmp_path):
    build_and_export(BBOX, custom(*FEATURES), tmp_path, osm_source=grid_pbf)
    assert fake_osm == []


def test_file_source_is_cropped_to_the_bbox(grid_pbf, tmp_path):
    corner = gpd.GeoDataFrame(geometry=[box(X0 - 10, Y0 - 10, X0 + 210, Y0 + 210)], crs=UTM)
    line = custom(([(X0 + 50, Y0), (X0 + 50, Y0 + 200)], ROAD_TAGS))

    result = build_and_export(corner, line, tmp_path, osm_source=grid_pbf)

    inside = {node_id(r, c) for r in range(3) for c in range(3)}
    assert set(result.osm_nodes.index) == inside


def test_large_area_allowed_with_a_file(grid_pbf, tmp_path):
    huge = gpd.GeoDataFrame(geometry=[box(X0 - 25_000, Y0 - 25_000, X0 + 25_000, Y0 + 25_000)],
                            crs=UTM)
    result = build_and_export(huge, custom(*FEATURES), tmp_path, osm_source=grid_pbf)
    assert set(GRID_NODES) <= set(result.osm_nodes.index)


def test_large_area_refused_without_a_file(build, fake_osm, tmp_path):
    huge = gpd.GeoDataFrame(geometry=[box(X0 - 25_000, Y0 - 25_000, X0 + 25_000, Y0 + 25_000)],
                            crs=UTM)
    with pytest.raises(InputError, match="osm_source="):
        build_and_export(huge, custom(*FEATURES), tmp_path)
    assert fake_osm == [], "Overpass was queried for a refused area"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(InputError, match="OSM file not found"):
        build_and_export(BBOX, custom(*FEATURES), tmp_path, osm_source=tmp_path / "nope.osm.pbf")


def test_file_that_is_not_osm_is_a_clear_error(tmp_path):
    bad = tmp_path / "bad.osm.pbf"
    bad.write_text("not an osm file")
    with pytest.raises(InputError, match="Can't read bad.osm.pbf"):
        build_and_export(BBOX, custom(*FEATURES), tmp_path, osm_source=bad)


def test_file_that_does_not_cover_the_bbox_is_refused(grid_pbf, tmp_path):
    far = 50_000  # 50 km east of the grid
    elsewhere = gpd.GeoDataFrame(geometry=[box(X0 + far, Y0, X0 + far + 500, Y0 + 500)], crs=UTM)
    line = custom(([(X0 + far + 100, Y0 + 100), (X0 + far + 400, Y0 + 400)], ROAD_TAGS))

    with pytest.raises(InputError, match="doesn't overlap"):
        build_and_export(elsewhere, line, tmp_path, osm_source=grid_pbf)


def test_ways_cut_at_the_extract_edge_keep_their_known_parts(tmp_path, caplog):
    """Way 10 references node 99, which the file doesn't contain."""
    source, area = tmp_path / "cut.osm.pbf", tmp_path / "area.osm"
    writer = osmium.SimpleWriter(str(source))
    for ref in (1, 2, 3, 4):
        writer.add_node(osmium.osm.mutable.Node(id=ref, location=(ref * 0.001, 0.0)))
    writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[1, 2, 99, 3, 4],
                                          tags={"highway": "residential"}))
    writer.close()

    with caplog.at_level(logging.WARNING):
        count = _extract_area(source, (-1, -1, 1, 1), "all", area)

    ways = [[n.ref for n in way.nodes] for way in osmium.FileProcessor(str(area), osmium.osm.WAY)]
    assert count == 2
    assert sorted(ways) == [[1, 2], [3, 4]]
    assert "cut at the extract's edge" in caplog.text
