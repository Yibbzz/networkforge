"""
Structural invariants on a full build of the demo area (tests/data):

    assert all_edges_have_valid_nodes
    assert no_u_equals_v
    assert all_custom_edges_are_connected
    assert custom_lines_unbroken

Run with: uv run pytest -m network tests/live/test_structural_invariants.py -v

Requires network access (build_network downloads OSM data for the
bounding box in tests/data/extent.geojson).
"""

import geopandas as gpd
import pytest

from networkforge.network import build_network
from networkforge.validation import (
    assert_all_custom_edges_are_connected,
    assert_all_edges_have_valid_nodes,
    assert_custom_lines_unbroken,
    assert_no_u_equals_v,
)

pytestmark = pytest.mark.network

TEST_TAGS = {
    "highway": "residential",
    "maxspeed": "30",
    "lanes": "2",
    "oneway": "no",
    "access": "yes",
}


@pytest.fixture(scope="module")
def built():
    """Build once and share across this module's tests (it's slow)."""
    bbox_gdf = gpd.read_file("tests/data/extent.geojson")
    custom_data_gdf = gpd.read_file("tests/data/custom_road_test.geojson")

    # strict=False here so a single bad edge doesn't stop us from also
    # seeing the OTHER assertions fail below with a clear message - in
    # normal use you want strict=True (the default) so build_network
    # itself fails fast.
    nodes, edges = build_network(
        bbox_gdf, custom_data_gdf, TEST_TAGS, network_type="all", strict=False,
    )
    return nodes, edges, len(custom_data_gdf.explode(index_parts=True))


def test_all_edges_have_valid_nodes(built):
    nodes, edges, _ = built
    assert_all_edges_have_valid_nodes(edges, nodes)


def test_no_u_equals_v(built):
    _nodes, edges, _ = built
    assert_no_u_equals_v(edges)


def test_all_custom_edges_are_connected(built):
    _nodes, edges, _ = built
    assert_all_custom_edges_are_connected(edges)


def test_custom_lines_unbroken(built):
    _nodes, edges, input_lines = built
    assert_custom_lines_unbroken(edges, input_lines)
