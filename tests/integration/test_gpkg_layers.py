"""
The GeoPackage's restrictions layer and the marks on the user's nodes:
what QGIS shows of turn restrictions and points.

The restrictions layer is drawn from the OSM file written from the same
tables, so it holds exactly the restrictions a router reads.
"""

import geopandas as gpd
import osmium
import pyogrio
import pytest
import shapely
from shapely.geometry import LineString, Point

from networkforge import build_network, osm, write_gpkg, write_osm
from networkforge.export import RESTRICTION_COLUMNS, RESTRICTION_REACH_M
from tests.integration.grid import (
    NO_LEFT_TURN,
    RESTRICTION_ID,
    UTM,
    VIA_WAY_RESTRICTION,
    X0,
    Y0,
    grid_with_via_way_restriction,
    node_id,
)


def xy(col, row):
    return (X0 + col * 100, Y0 + row * 100)


# East along Row 1 Street, left (north) into Column 1 Avenue at node 7.
TURN_AT_7 = [xy(0.5, 1), xy(1, 1), xy(1, 1.5)]


def layer(nodes, edges, tmp_path, name="restrictions"):
    path = tmp_path / "network.gpkg"
    write_gpkg(nodes, edges, path)
    return gpd.read_file(path, layer=name)


def relations_in_pbf(nodes, edges, tmp_path):
    path = tmp_path / "network.osm.pbf"
    write_osm(nodes, edges, path)
    return {r.id: dict(r.tags) for r in osmium.FileProcessor(str(path), osmium.osm.RELATION)
            if r.tags.get("type") == "restriction"}


def node_xy(nodes, node):
    return nodes.geometry.loc[node]


# The layer is drawn from the OSM file's coordinates (7 decimals: ~1 cm).
CM = 0.01


def near(a, b):
    return Point(a).distance(Point(b)) < CM


# ---------------------------------------------------------------------
# Restrictions
# ---------------------------------------------------------------------

def test_before_shows_openstreetmaps_restriction(build, tmp_path):
    result = build([(TURN_AT_7, {"restriction": "no_left_turn"})])
    before = layer(result.osm_nodes, result.osm_edges, tmp_path)

    assert list(before.relation_id) == [RESTRICTION_ID]
    (row,) = before.itertuples()
    assert row.restriction == "no_left_turn" and row.custom is None
    assert row.via_node == NO_LEFT_TURN[1] and row.via_ways is None
    # 20 m along Row 3 Street to node 18, then 20 m north up Column 2 Avenue.
    line = row.geometry
    assert near(line.coords[0], (X0 + 200 - RESTRICTION_REACH_M, Y0 + 300))
    assert line.distance(node_xy(result.osm_nodes, NO_LEFT_TURN[1])) < CM
    assert near(line.coords[-1], (X0 + 200, Y0 + 300 + RESTRICTION_REACH_M))
    assert line.length == pytest.approx(2 * RESTRICTION_REACH_M, abs=CM)


def test_after_adds_the_users_restriction_as_the_pbf_has_it(build, tmp_path):
    result = build([(TURN_AT_7, {"restriction": "no_left_turn"})])
    after = layer(result.nodes, result.edges, tmp_path)
    in_pbf = relations_in_pbf(result.nodes, result.edges, tmp_path)

    assert sorted(after.relation_id) == sorted(in_pbf)
    mine = after[after.custom == "yes"]
    assert len(mine) == 1 and RESTRICTION_ID in set(after.relation_id)
    (row,) = mine.itertuples()
    assert row.restriction == "no_left_turn" and row.via_node == node_id(1, 1)
    # It runs along Row 1 Street to node 7, then up Column 1 Avenue.
    line = row.geometry
    assert line.distance(Point(xy(1, 1))) < CM
    assert near(line.coords[0], (X0 + 100 - RESTRICTION_REACH_M, Y0 + 100))
    assert near(line.coords[-1], (X0 + 100, Y0 + 100 + RESTRICTION_REACH_M))
    assert "nf:custom" not in after.columns and "type" not in after.columns


def test_vehicle_restrictions_and_exceptions_are_columns(build, tmp_path):
    result = build([(TURN_AT_7, {"restriction:hgv": "no_left_turn", "except": "bus"})])
    after = layer(result.nodes, result.edges, tmp_path)
    (row,) = after[after.custom == "yes"].to_dict("records")
    assert row["restriction:hgv"] == "no_left_turn" and row["except"] == "bus"


def test_via_way_restriction_runs_along_the_via_way(build, tmp_path, monkeypatch):
    monkeypatch.setattr(osm, "_download_elements",
                        lambda polygon, kind: grid_with_via_way_restriction())
    result = build([([xy(0, 0), xy(0.5, 0.5)], {"highway": "residential"})])
    after = layer(result.nodes, result.edges, tmp_path)

    (row,) = after[after.relation_id == VIA_WAY_RESTRICTION].to_dict("records")
    assert gpd.pd.isna(row["via_node"]) and row["via_ways"]
    for node in (18, 19, 20):  # Row 3 Street, the via way
        assert row["geometry"].distance(node_xy(result.nodes, node)) < CM


def test_no_restrictions_is_an_empty_line_layer(tmp_path):
    lines = gpd.GeoDataFrame(
        {"highway": ["residential", "residential"]},
        geometry=[LineString([xy(0, 0), xy(2, 0)]), LineString([xy(1, -1), xy(1, 1)])],
        crs=UTM)
    nodes, edges = build_network(None, lines, standalone=True)
    path = tmp_path / "network.gpkg"
    write_gpkg(nodes, edges, path)

    layers = dict(pyogrio.list_layers(path))
    assert layers["restrictions"] == "LineString"
    empty = gpd.read_file(path, layer="restrictions")
    assert empty.empty and set(RESTRICTION_COLUMNS) <= set(empty.columns)


def test_standalone_restrictions_are_all_custom(tmp_path):
    lines = gpd.GeoDataFrame(
        [{"highway": "residential"}, {"highway": "residential"},
         {"restriction": "no_right_turn"}],
        geometry=[LineString([xy(0, 0), xy(2, 0)]), LineString([xy(1, -1), xy(1, 1)]),
                  LineString([xy(0.5, 0), xy(1, 0), xy(1, -0.5)])],
        crs=UTM)
    nodes, edges = build_network(None, lines, standalone=True)
    restrictions = layer(nodes, edges, tmp_path)
    assert list(restrictions.custom) == ["yes"]
    assert list(restrictions.restriction) == ["no_right_turn"]


# ---------------------------------------------------------------------
# The user's nodes
# ---------------------------------------------------------------------

def test_points_mark_their_nodes_changed_or_new(build, tmp_path):
    result = build([
        (Point(xy(2, 1)), {"highway": "traffic_signals"}),   # on OSM's node 8
        (Point(X0 + 150, Y0 + 300), {"barrier": "bollard"}),  # mid-block: a new node
    ])
    nodes = layer(result.nodes, result.edges, tmp_path, "nodes").set_index("osmid")

    assert nodes.loc[node_id(1, 2), "modified"] == "yes"
    assert gpd.pd.isna(nodes.loc[node_id(1, 2), "custom"])
    (new,) = nodes.index[nodes.custom == "yes"]
    assert nodes.loc[new, "barrier"] == "bollard" and new > 100
    assert nodes.modified.notna().sum() == 1


def test_nodes_without_points_have_no_marks(build, tmp_path):
    result = build([([xy(0, 0), xy(0.5, 0.5)], {"highway": "residential"})])
    nodes = layer(result.nodes, result.edges, tmp_path, "nodes")
    assert "modified" not in nodes.columns and "custom" not in nodes.columns


def test_limits_tolls_and_entrances_are_node_columns(build, tmp_path):
    result = build([
        (Point(X0 + 150, Y0 + 300),
         {"barrier": "height_restrictor", "maxheight": "2.1"}),
        (Point(X0 + 250, Y0 + 300), {"barrier": "toll_booth", "toll": "yes", "hgv": "no"}),
    ])
    nodes = layer(result.nodes, result.edges, tmp_path, "nodes")
    assert set(nodes.maxheight.dropna()) == {"2.1"}
    assert set(nodes.toll.dropna()) == {"yes"} and set(nodes.hgv.dropna()) == {"no"}


def test_restriction_geometry_is_valid_lines(build, tmp_path):
    result = build([(TURN_AT_7, {"restriction": "no_left_turn"})])
    after = layer(result.nodes, result.edges, tmp_path)
    assert shapely.is_valid(after.geometry.values).all()
    assert (after.geometry.geom_type == "LineString").all()
