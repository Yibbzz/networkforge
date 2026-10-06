"""Points in the custom layer tag the network's nodes, offline."""

import logging

import geopandas as gpd
import osmium
import pytest
from shapely.geometry import LineString, MultiPoint, Point

from networkforge import build_network, write_gpkg, write_osm
from networkforge.errors import InputError, InvalidTagsError
from tests.integration.grid import X0, Y0, node_id

BOLLARD = {"barrier": "bollard"}
MID_BLOCK = Point(X0 + 150, Y0 + 100)  # Row 1 Street, between nodes 7 and 8


def osm_of(result, tmp_path, which="custom"):
    path = tmp_path / f"{which}.osm.pbf"
    nodes, edges = ((result.nodes, result.edges) if which == "custom"
                    else (result.osm_nodes, result.osm_edges))
    write_osm(nodes, edges, path)
    tags = {n.id: dict(n.tags) for n in osmium.FileProcessor(str(path), osmium.osm.NODE)}
    ways = {w.id: [n.ref for n in w.nodes]
            for w in osmium.FileProcessor(str(path), osmium.osm.WAY)}
    return tags, ways


def test_point_mid_block_cuts_the_street_with_a_tagged_node(build, tmp_path):
    result = build([(MID_BLOCK, BOLLARD)])
    tags, ways = osm_of(result, tmp_path)

    (new,) = [n for n, t in tags.items() if t == BOLLARD and n > 100]
    (row_1,) = [refs for refs in ways.values() if node_id(1, 1) in refs and new in refs]
    assert row_1[row_1.index(new) - 1: row_1.index(new) + 2] == [node_id(1, 1), new, node_id(1, 2)]
    assert result.nodes.geometry.loc[new].distance(MID_BLOCK) < 1e-6


def test_the_before_network_is_untouched(build, tmp_path):
    result = build([(MID_BLOCK, BOLLARD)])
    before_tags, before_ways = osm_of(result, tmp_path, "osm")
    assert not any(t == BOLLARD and n > 100 for n, t in before_tags.items())
    assert len(result.osm_edges) < len(result.edges)


def test_point_near_a_junction_tags_the_junction(build, tmp_path):
    result = build([(Point(X0 + 100.4, Y0 + 100.3), {"highway": "traffic_signals"})])
    tags, _ = osm_of(result, tmp_path)

    assert tags[node_id(1, 1)] == {"highway": "traffic_signals"}
    assert len(result.nodes) == len(result.osm_nodes)  # no new node


def test_point_on_a_new_line(build, tmp_path):
    line = [(X0, Y0 + 50), (X0 + 100, Y0 + 50)]
    result = build([(line, {"highway": "residential"}),
                    (Point(X0 + 40, Y0 + 50.5), {"barrier": "gate", "access": "no"})])
    tags, ways = osm_of(result, tmp_path)

    (gate,) = [n for n, t in tags.items() if t.get("barrier") == "gate"]
    assert tags[gate] == {"barrier": "gate", "access": "no"}
    (custom,) = [refs for refs in ways.values() if gate in refs]
    assert len(custom) == 3 and custom[1] == gate


def test_two_points_on_one_block_and_a_multipoint(build, tmp_path):
    result = build([(MultiPoint([(X0 + 120, Y0 + 100), (X0 + 180, Y0 + 100)]), BOLLARD)])
    tags, ways = osm_of(result, tmp_path)

    new = sorted(n for n, t in tags.items() if t == BOLLARD and n > 100)
    assert len(new) == 2
    (row_1,) = [refs for refs in ways.values() if set(new) <= set(refs)]
    start = row_1.index(node_id(1, 1))
    assert row_1[start:start + 4] == [node_id(1, 1), *new, node_id(1, 2)]


def test_existing_node_tag_deleted(build, tmp_path):
    """Node 3 is a bollard in OSM; a point on it with remove_tags=barrier takes it out."""
    result = build([(Point(X0 + 200, Y0), {"remove_tags": "barrier"})])
    after, _ = osm_of(result, tmp_path)
    before, _ = osm_of(result, tmp_path, "osm")
    assert before[node_id(0, 2)] == BOLLARD and after[node_id(0, 2)] == {}


def test_deleting_a_tag_the_node_does_not_have_warns(build, caplog):
    with caplog.at_level(logging.WARNING):
        result = build([(Point(X0 + 100, Y0 + 100), {"remove_tags": "barrier"})])
    assert "changes nothing" in caplog.text
    assert result.edges.attrs["networkforge_node_changes"] == {}


def test_points_in_the_geopackage_nodes(build, tmp_path):
    result = build([(MID_BLOCK, BOLLARD)])
    write_gpkg(result.nodes, result.edges, tmp_path / "after.gpkg")
    nodes = gpd.read_file(tmp_path / "after.gpkg", layer="nodes")
    assert (nodes["barrier"] == "bollard").sum() == 2  # the grid's own and the new one


@pytest.mark.parametrize("geometry, tags, message", [
    (Point(X0 + 50, Y0 + 50), BOLLARD, "not within 1.0 m of a street"),
    (MID_BLOCK, {"remove_tags": "barrier"}, "no node within"),
])
def test_points_that_cant_be_placed(build, geometry, tags, message):
    with pytest.raises(InputError, match=message):
        build([(geometry, tags)])


@pytest.mark.parametrize("tags, message", [
    ({"note": "a point"}, "a point needs node tags"),
    ({"highway": "residential"}, "not a value for a point"),
    ({"barrier": "gate", "access": "maybe"}, "not a valid OSM access value"),
    ({"barrier": "bollard", "remove_tags": "barrier"}, "both given a value"),
])
def test_point_tag_problems(build, tags, message):
    with pytest.raises(InvalidTagsError, match=message):
        build([(MID_BLOCK, tags)])


def test_point_warnings(build, caplog):
    with caplog.at_level(logging.WARNING):
        build([(MID_BLOCK, {"barrier": "bolard"}),
               (Point(X0 + 350, Y0 + 100), {"access": "no"})])
    assert "barrier='bolard' is not a value routers know - did you mean 'bollard'?" in caplog.text
    assert "access tags but no barrier" in caplog.text


def test_point_on_a_bridge_over_a_street_needs_its_layer(build):
    """A new bridge crossing Row 1 Street at x=150: a point there is on both."""
    bridge = [(X0 + 150, Y0 + 50), (X0 + 150, Y0 + 150)]
    tags = {"highway": "residential", "bridge": "yes", "layer": "1"}
    with pytest.raises(InputError, match="lies on 2 streets"):
        build([(bridge, tags), (MID_BLOCK, BOLLARD)])

    result = build([(bridge, tags), (MID_BLOCK, {**BOLLARD, "layer": "1"})])
    edges = result.edges
    nodes = result.nodes
    (new,) = [n for n in nodes.index if n > 100 and nodes.at[n, "barrier"] == "bollard"]
    on = edges[(edges.u == new) | (edges.v == new)]
    assert set(on["custom"]) == {"yes"}  # the bridge, not Row 1 Street


def test_points_in_a_standalone_network(tmp_path):
    layer = gpd.GeoDataFrame(
        {"highway": ["residential", "residential", None], "barrier": [None, None, "bollard"]},
        geometry=[LineString([(0, 0), (200, 0)]), LineString([(100, -100), (100, 100)]),
                  Point(50, 0.3)],
        crs="EPSG:32630",
    )
    nodes, edges = build_network(None, layer, standalone=True)
    write_osm(nodes, edges, tmp_path / "standalone.osm")
    assert '<tag k="barrier" v="bollard"' in (tmp_path / "standalone.osm").read_text()
