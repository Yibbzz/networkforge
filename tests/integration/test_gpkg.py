"""
The QGIS GeoPackage: analysis columns, one row per street, exact
topology - and routing on it the way QGIS does gives the engine's routes.
"""

import geopandas as gpd
import networkx as nx
import pyogrio
import pytest
import shapely

from networkforge import write_gpkg
from networkforge.export import analysis_edges
from tests.helpers import ROUTING_MODES, qgis_graph
from tests.integration.grid import (
    BUS_GATE,
    CYCLEWAY,
    DIAGONAL,
    FOOTWAY,
    GRID_NODES,
    MOTORWAY_NODES,
    NO_ACCESS,
    ROAD_ACROSS,
    ROAD_TAGS,
    node_id,
)


@pytest.fixture
def result(build):
    return build([(ROAD_ACROSS, ROAD_TAGS), (DIAGONAL, {"highway": "cycleway"})])


@pytest.fixture
def edges(result):
    return analysis_edges(result.nodes, result.edges)


def rows_between(edges, pair):
    a, b = sorted(pair)
    return edges[((edges.u == a) & (edges.v == b)) | ((edges.u == b) & (edges.v == a))]


def lengths(graph):
    return dict(nx.all_pairs_dijkstra_path_length(graph, weight="length"))


def test_writes_nodes_and_edges_layers(result, tmp_path):
    path = tmp_path / "network.gpkg"
    write_gpkg(result.nodes, result.edges, path)
    write_gpkg(result.nodes, result.edges, path)  # replaces, doesn't append

    assert sorted(pyogrio.list_layers(path)[:, 0]) == ["edges", "nodes"]
    layer = gpd.read_file(path, layer="edges")
    assert len(layer) == len(analysis_edges(result.nodes, result.edges))
    for column in ("car", "bike", "walk", "speed_kph", "length_m", "car_minutes",
                   "bike_minutes", "walk_minutes", "car_direction", "bike_direction"):
        assert column in layer.columns
    assert not {"split", "key", "reversed"} & set(layer.columns)


def test_one_row_per_two_way_street(edges):
    assert len(rows_between(edges, {node_id(0, 0), node_id(0, 1)})) == 1


def test_geometry_runs_exactly_between_its_nodes(result, edges):
    starts = shapely.get_point(edges.geometry.to_numpy(), 0)
    ends = shapely.get_point(edges.geometry.to_numpy(), -1)
    nodes = result.nodes.geometry
    assert shapely.equals(starts, nodes.loc[edges.u.astype(int)].to_numpy()).all()
    assert shapely.equals(ends, nodes.loc[edges.v.astype(int)].to_numpy()).all()


def test_footway_cut_by_the_custom_road_is_two_walk_only_rows(edges):
    """ROAD_ACROSS crosses the footway: two pieces, and no leftover uncut copy."""
    assert rows_between(edges, FOOTWAY).empty
    pieces = edges[(edges.highway == "footway") & (edges.custom != "yes")]
    assert len(pieces) == 2
    assert {(row.car, row.bike, row.walk) for row in pieces.itertuples()} == {(False, False, True)}


@pytest.mark.parametrize("pair, car, bike, walk", [
    (CYCLEWAY, False, True, False),
    (BUS_GATE, False, True, True),
    (NO_ACCESS, False, False, True),
    ({node_id(0, 0), node_id(0, 1)}, True, True, True),
])
def test_mode_columns(edges, pair, car, bike, walk):
    (row,) = rows_between(edges, pair).itertuples()
    assert (row.car, row.bike, row.walk) == (car, bike, walk)


def test_directions(edges):
    (motorway,) = rows_between(edges, set(MOTORWAY_NODES)).itertuples()
    assert motorway.car_direction == "forward"  # one-way, u -> v
    custom = edges[edges.custom == "yes"]
    assert set(custom.car_direction) == {"both"}


@pytest.mark.parametrize("oneway, expected", [("yes", "forward"), ("-1", "backward")])
def test_custom_oneway_direction(build, oneway, expected):
    result = build([(ROAD_ACROSS, {**ROAD_TAGS, "oneway": oneway})])
    custom = analysis_edges(result.nodes, result.edges).query("custom == 'yes'")
    assert set(custom.car_direction) == {expected}


def test_contraflow_cycling(build):
    result = build([(ROAD_ACROSS, {**ROAD_TAGS, "oneway": "yes", "oneway:bicycle": "no"})])
    custom = analysis_edges(result.nodes, result.edges).query("custom == 'yes'")
    assert set(custom.car_direction) == {"forward"}
    assert set(custom.bike_direction) == {"both"}


def test_speeds_and_times(edges):
    (street,) = rows_between(edges, {node_id(0, 0), node_id(0, 1)}).itertuples()
    assert street.speed_kph == pytest.approx(48.3)  # 30 mph
    assert street.length_m == pytest.approx(100, abs=0.01)
    assert street.car_minutes == pytest.approx(100 / 1000 / 48.28 * 60, abs=1e-3)
    assert street.walk_minutes == pytest.approx(1.2)  # 100 m at 5 km/h

    custom_road = edges[(edges.custom == "yes") & (edges.highway == "primary")]
    assert list(custom_road.speed_kph) == pytest.approx([48.3] * len(custom_road))  # 30 mph


@pytest.mark.parametrize("mode", ROUTING_MODES)
def test_routing_on_the_gpkg_matches_the_engine(result, edges, mode):
    """
    Shortest routes on the layer (as QGIS reads it) equal the engine's.
    Lengths differ only by measurement: the GeoPackage's metric CRS vs
    OSMnx's great-circle on a sphere of mean Earth radius (~0.3% at 56N).
    """
    ours = lengths(qgis_graph(edges, mode))
    engine = lengths(result.graph("custom", mode))

    for a in GRID_NODES:
        for b in GRID_NODES:
            if a == b:
                continue
            expected = engine.get(a, {}).get(b)
            actual = ours.get(a, {}).get(b)
            if expected is None:
                assert actual is None, f"{mode} {a}->{b}: QGIS finds a route the engine doesn't"
            else:
                assert actual == pytest.approx(expected, rel=5e-3), f"{mode} {a}->{b}"

