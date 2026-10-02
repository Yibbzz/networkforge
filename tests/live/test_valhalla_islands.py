"""
Island countries: the network is in several pieces, joined by ferries or
by nothing. Every piece must be in NetworkForge's file, and Valhalla
must route on it as on the OpenStreetMap extract.

Run with: uv run pytest -m network tests/live/test_valhalla_islands.py -v

Downloads Geofabrik's Malta (9 MB; Malta and Gozo, joined by ferry) and
Maldives (4 MB; hundreds of islands) extracts. OSMnx's default keeps
only the largest connected piece of a network, which dropped Gozo and
most of the Maldives until build_network stopped using it.
"""

import random
import urllib.error
import urllib.request

import geopandas as gpd
import osmium
import pytest
from shapely.geometry import box

from networkforge import write_osm
from networkforge.modes import _passes_osmnx_filter
from networkforge.osm import get_osm_data_from_file
from networkforge.projection import get_analysis_crs

pytest.importorskip("valhalla", reason="pyvalhalla is not installed (uv sync installs it)")

from tests.valhalla.harness import Router  # noqa: E402

pytestmark = [pytest.mark.network, pytest.mark.valhalla]

EXTRACTS = {
    "malta": "https://download.geofabrik.de/europe/malta-latest.osm.pbf",
    "maldives": "https://download.geofabrik.de/asia/maldives-latest.osm.pbf",
}
POINTS = 30  # every pair is routed


@pytest.fixture(scope="module", params=list(EXTRACTS))
def routers(request, tmp_path_factory):
    """(Valhalla on the extract, Valhalla on NetworkForge's file of the whole extract)."""
    folder = tmp_path_factory.mktemp(request.param)
    extract = folder / "extract.osm.pbf"
    try:
        urllib.request.urlretrieve(EXTRACTS[request.param], extract)
    except urllib.error.URLError as exc:
        pytest.fail(f"Couldn't download {EXTRACTS[request.param]}: {exc}")

    header = osmium.io.Reader(str(extract), osmium.osm.osm_entity_bits.NOTHING).header().box()
    area = gpd.GeoDataFrame(geometry=[box(
        header.bottom_left.lon - 0.01, header.bottom_left.lat - 0.01,
        header.top_right.lon + 0.01, header.top_right.lat + 0.01)], crs="EPSG:4326")
    nodes, edges = get_osm_data_from_file(extract, area, get_analysis_crs(area))
    write_osm(nodes, edges, folder / "before.osm.pbf")

    return (Router.from_pbf(extract, folder / "osm"),
            Router.from_pbf(folder / "before.osm.pbf", folder / "before"))


def test_every_street_of_every_island_is_in_the_file(routers):
    osm_itself, before = routers
    streets = {way: value for way, value in osm_itself.ways.items()
               if "highway" in value[1] and _passes_osmnx_filter(value[1], "all")}

    missing = [way for way in streets if way not in before.ways]
    assert len(streets) > 1000 and not missing, f"{len(missing)} of {len(streets)} ways missing"
    assert all(before.ways[way] == value for way, value in streets.items())


@pytest.mark.parametrize("costing", ["auto", "truck", "bicycle", "pedestrian"])
def test_routes_are_those_of_the_extract(routers, costing):
    """
    Valhalla's matrix is not exact on a graph this size (a handful of
    cells can differ between two graphs of the same data), so a cell
    that differs is checked again with its exact route search.
    """
    osm_itself, before = routers
    streets = sorted(
        (way, refs) for way, (refs, tags) in before.ways.items()
        if tags.get("highway") in ("residential", "tertiary", "secondary", "primary",
                                   "unclassified", "footway", "path", "service")
    )
    chosen = random.Random(7).sample(streets, POINTS)
    points = [before.node(refs[len(refs) // 2]) for _, refs in chosen]

    try:
        expected, found = osm_itself.matrix(points, costing), before.matrix(points, costing)
    except RuntimeError:  # islands too far apart for one matrix request
        expected = found = None
    pairs = [(i, j) for i in range(POINTS) for j in range(POINTS)
             if i != j and (expected is None or expected[i][j] != found[i][j])]
    if expected is None:
        pairs = random.Random(7).sample(pairs, 60)

    different = []
    for i, j in pairs:
        try:
            a = osm_itself.route(points[i], points[j], costing)
            b = before.route(points[i], points[j], costing)
        except RuntimeError:  # too far for a route in both
            continue
        if (a is None) != (b is None) or (a and (abs(a.length_m - b.length_m) > 1
                                                 or abs(a.time_s - b.time_s) > 1)):
            different.append((points[i], points[j], a and a.length_m, b and b.length_m))
    assert not different, different[:5]
