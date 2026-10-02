"""Fixtures for the offline integration tests (synthetic OSM grid)."""

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from networkforge import osm
from tests.helpers import build_and_export
from tests.integration.grid import BBOX, UTM, grid_elements


@pytest.fixture
def fake_osm(monkeypatch):
    """Replace the Overpass download with grid_elements(); counts calls."""
    calls = []

    def fake_download(polygon, network_type):
        calls.append(network_type)
        return grid_elements()

    monkeypatch.setattr(osm, "_download_elements", fake_download)
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
