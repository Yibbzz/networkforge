"""
Fixtures for the Valhalla tests: build a before / after network on the
synthetic grid (tests/integration/grid.py), build Valhalla's graph from
each PBF, and route on both.

The tests need pyvalhalla (`uv sync` installs the pinned version); they
are skipped where it isn't installed.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString

pytest.importorskip("valhalla", reason="pyvalhalla is not installed (uv sync installs it)")

from networkforge import build_network, osm, write_osm  # noqa: E402
from tests.integration.grid import BBOX, SPACING, UTM, X0, Y0, grid_elements  # noqa: E402
from tests.valhalla.harness import LonLat, Route, Router  # noqa: E402

_TO_WGS84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)


def at(column: float, row: float) -> LonLat:
    """(lon, lat) of a grid position: at(2, 1) is node_id(1, 2); at(0.5, 0) is mid-block."""
    return _TO_WGS84.transform(X0 + column * SPACING, Y0 + row * SPACING)


def xy(column: float, row: float) -> tuple[float, float]:
    """The same grid position in the custom data's CRS (for drawing lines)."""
    return (X0 + column * SPACING, Y0 + row * SPACING)


# The grid's turn restriction (NO_LEFT_TURN): driving east along Row 3
# Street, no left turn north at node 18. A trip from half a block before
# the turn to half a block after it is 100 m if the turn is allowed, and
# 300 m round the block if not - no other route ties with either.
BEFORE_THE_TURN, AFTER_THE_TURN = at(1.5, 3), at(2, 3.5)


def turn_metres(router: Router, costing: str) -> float:
    """Length of the shortest trip across the restricted turn."""
    return router.route(BEFORE_THE_TURN, AFTER_THE_TURN, costing, shortest=True).length_m


@dataclass
class Scenario:
    """A build on the grid and Valhalla's graph of its before and after PBF."""

    nodes: gpd.GeoDataFrame
    edges: gpd.GeoDataFrame
    before: Router
    after: Router

    @property
    def custom_ways(self) -> list[int]:
        """Ids of the ways written for the custom lines, in feature order."""
        return sorted(self.after.custom_way_ids())

    def route(self, which: str, start: LonLat, end: LonLat, costing: str, **options) -> Route:
        return getattr(self, which).route(start, end, costing, **options)

    def uses_custom(self, start: LonLat, end: LonLat, costing: str, **options) -> bool:
        """Does the after route travel along any custom way?"""
        route = self.after.route(start, end, costing, **options)
        return route is not None and any(route.uses(way) for way in self.custom_ways)


def custom_layer(features) -> gpd.GeoDataFrame:
    """features = [(coords or [coords, ...] for a multi-line, tags), ...] in grid CRS."""
    geometries = [
        MultiLineString(coords) if isinstance(coords[0][0], tuple | list) else LineString(coords)
        for coords, _ in features
    ]
    return gpd.GeoDataFrame([tags for _, tags in features], geometry=geometries, crs=UTM)


def build_scenario(features, folder: Path, elements=None, before=None, **build_kwargs) -> Scenario:
    """
    Build on the grid (or on `elements`), write both PBFs into `folder`
    and build Valhalla's graph of each. `before`: {file hash: Router},
    to reuse the before graph between builds on the same OSM data.
    """
    data = grid_elements() if elements is None else elements
    with mock.patch.object(osm, "_download_elements", lambda polygon, kind: data):
        nodes, edges, osm_nodes, osm_edges = build_network(
            BBOX, custom_layer(features), return_source_osm=True, **build_kwargs)
    write_osm(osm_nodes, osm_edges, folder / "before.osm.pbf")
    write_osm(nodes, edges, folder / "after.osm.pbf")

    before = {} if before is None else before
    digest = hashlib.sha256((folder / "before.osm.pbf").read_bytes()).hexdigest()
    if digest not in before:
        before[digest] = Router.from_pbf(folder / "before.osm.pbf", folder / "before")
    return Scenario(nodes, edges, before=before[digest],
                    after=Router.from_pbf(folder / "after.osm.pbf", folder / "after"))


@pytest.fixture(scope="session")
def scenario(tmp_path_factory):
    """
    scenario(features, elements=None, **build_kwargs) -> Scenario.
    `elements` replaces the grid's OSM data (default grid_elements()).
    Identical calls share one build for the whole test session.
    """
    built, before = {}, {}

    def _scenario(features, elements=None, **build_kwargs) -> Scenario:
        key = json.dumps([features, elements, build_kwargs], sort_keys=True, default=str)
        if key not in built:
            folder = Path(tmp_path_factory.mktemp("valhalla"))
            built[key] = build_scenario(features, folder, elements, before, **build_kwargs)
        return built[key]

    return _scenario


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/valhalla/" in item.nodeid:
            item.add_marker(pytest.mark.valhalla)
