"""Fixtures for the offline integration tests (synthetic OSM grid)."""

import geopandas as gpd
import osmnx as ox
import pytest
from shapely.geometry import LineString

from tests.helpers import build_and_export
from tests.integration.grid import BBOX, UTM, synthetic_graph


@pytest.fixture
def fake_osm(monkeypatch):
    """Replace the Overpass download with synthetic_graph(); counts calls."""
    calls = []

    def fake_graph_from_bbox(bbox, network_type="all", simplify=True, **kwargs):
        calls.append(network_type)
        return synthetic_graph()

    monkeypatch.setattr(ox, "graph_from_bbox", fake_graph_from_bbox)
    return calls


@pytest.fixture
def build(fake_osm, tmp_path):
    """build(features, **build_kwargs) with features = [(coords, tags), ...]."""
    def _build(features, **build_kwargs):
        custom = gpd.GeoDataFrame(
            [tags for _, tags in features],
            geometry=[LineString(coords) for coords, _ in features],
            crs=UTM,
        )
        return build_and_export(BBOX, custom, tmp_path, **build_kwargs)
    return _build
