"""
Real data: Valhalla must route on NetworkForge's "before" file as it
routes on the OpenStreetMap extract the file was made from, and a
custom footbridge must change walking trips only.

Run with: uv run pytest -m network tests/live/test_valhalla_real_data.py -v

Downloads Geofabrik's Monaco extract (under 1 MB). The grid tests in
tests/valhalla prove the rules; this proves nothing is lost on real
data, with its turn restrictions, long ways and thousands of tags.
"""

import random
import urllib.error
import urllib.request

import geopandas as gpd
import osmium
import pytest
from shapely.geometry import LineString, box

from networkforge import build_network, write_gpkg, write_osm

pytest.importorskip("valhalla", reason="pyvalhalla is not installed (uv sync installs it)")

from tests.valhalla.harness import Router  # noqa: E402

pytestmark = [pytest.mark.network, pytest.mark.valhalla]

EXTRACT_URL = "https://download.geofabrik.de/europe/monaco-latest.osm.pbf"
TRIPS = 30  # points; every pair is routed

# A footbridge across Port Hercule, from quay to quay.
FOOTBRIDGE = LineString([(7.4208, 43.7353), (7.4252, 43.7338)])


@pytest.fixture(scope="module")
def extract(tmp_path_factory):
    path = tmp_path_factory.mktemp("geofabrik") / "monaco.osm.pbf"
    try:
        urllib.request.urlretrieve(EXTRACT_URL, path)
    except urllib.error.URLError as exc:
        pytest.fail(f"Couldn't download {EXTRACT_URL}: {exc}")
    return path


@pytest.fixture(scope="module")
def routers(extract, tmp_path_factory):
    """Valhalla on the extract itself, and on NetworkForge's before and after files."""
    folder = tmp_path_factory.mktemp("monaco")
    header = osmium.io.Reader(str(extract), osmium.osm.osm_entity_bits.NOTHING).header().box()
    whole_file = gpd.GeoDataFrame(geometry=[box(
        header.bottom_left.lon - 0.01, header.bottom_left.lat - 0.01,
        header.top_right.lon + 0.01, header.top_right.lat + 0.01)], crs="EPSG:4326")
    custom = gpd.GeoDataFrame({"highway": ["footway"], "bridge": ["yes"], "layer": ["1"]},
                              geometry=[FOOTBRIDGE], crs="EPSG:4326")

    nodes, edges, osm_nodes, osm_edges = build_network(
        whole_file, custom, osm_source=extract, return_source_osm=True)
    write_osm(osm_nodes, osm_edges, folder / "before.osm.pbf")
    write_osm(nodes, edges, folder / "after.osm.pbf")

    return {
        "osm": Router.from_pbf(extract, folder / "osm"),
        "before": Router.from_pbf(folder / "before.osm.pbf", folder / "before"),
        "after": Router.from_pbf(folder / "after.osm.pbf", folder / "after"),
    }


@pytest.fixture(scope="module")
def points(routers):
    """Mid-points of ordinary streets, the same every run."""
    streets = sorted(
        (way_id, refs) for way_id, (refs, tags) in routers["before"].ways.items()
        if tags.get("highway") in ("residential", "tertiary", "secondary", "primary",
                                   "unclassified")
    )
    chosen = random.Random(1).sample(streets, TRIPS)
    return [routers["before"].node(refs[len(refs) // 2]) for _, refs in chosen]


def differing(a, b, tolerance=0.01):
    """Share of trips whose distance differs by more than `tolerance` (or exists in one only)."""
    pairs = [(x, y) for row_a, row_b in zip(a, b, strict=True)
             for x, y in zip(row_a, row_b, strict=True)]
    differ = sum(
        (x is None) != (y is None)
        or (x is not None and abs(x[0] - y[0]) > tolerance * max(x[0], 1))
        for x, y in pairs
    )
    return differ / len(pairs)


@pytest.mark.parametrize("costing", ["auto", "bus", "truck", "bicycle"])
def test_before_routes_vehicles_exactly_like_the_extract(routers, points, costing):
    assert routers["before"].matrix(points, costing) == routers["osm"].matrix(points, costing)


def test_before_routes_walkers_almost_like_the_extract(routers, points):
    """
    Not exactly: squares mapped as areas, platforms and the like are not
    part of NetworkForge's network (OSMnx's filter), and Valhalla walks
    along them. Fewer than one trip in ten may differ by over 1%.
    """
    assert differing(routers["before"].matrix(points, "pedestrian"),
                     routers["osm"].matrix(points, "pedestrian")) < 0.10


@pytest.mark.parametrize("costing", ["auto", "bus", "truck", "bicycle"])
def test_footbridge_changes_nothing_for_vehicles(routers, points, costing):
    before = routers["before"].matrix(points, costing, shortest=True)
    after = routers["after"].matrix(points, costing, shortest=True)
    assert differing(before, after, tolerance=0.001) == 0


def test_footbridge_shortens_the_walk_across_the_harbour(routers):
    start, end = FOOTBRIDGE.coords[0], FOOTBRIDGE.coords[-1]
    before = routers["before"].route(start, end, "pedestrian")
    after = routers["after"].route(start, end, "pedestrian")
    (bridge,) = routers["after"].custom_way_ids()

    assert after.way_ids == (bridge,)
    assert after.length_m < before.length_m - 200

    driving = routers["after"].route(start, end, "auto")
    assert driving is None or not driving.uses(bridge)


# ---------------------------------------------------------------------
# Changing an existing street, the way a QGIS user would
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def closed_street(extract, tmp_path_factory):
    """
    Build once, open the before GeoPackage, copy the rows of one named
    two-way street, set access=no, and build again with those rows as
    the custom layer. Returns (before router, after router, way id).
    """
    folder = tmp_path_factory.mktemp("closure")
    header = osmium.io.Reader(str(extract), osmium.osm.osm_entity_bits.NOTHING).header().box()
    area = gpd.GeoDataFrame(geometry=[box(
        header.bottom_left.lon - 0.01, header.bottom_left.lat - 0.01,
        header.top_right.lon + 0.01, header.top_right.lat + 0.01)], crs="EPSG:4326")
    bridge = gpd.GeoDataFrame({"highway": ["footway"], "bridge": ["yes"], "layer": ["1"]},
                              geometry=[FOOTBRIDGE], crs="EPSG:4326")

    _, _, osm_nodes, osm_edges = build_network(area, bridge, osm_source=extract,
                                               return_source_osm=True)
    write_gpkg(osm_nodes, osm_edges, folder / "before.gpkg")
    layer = gpd.read_file(folder / "before.gpkg", layer="edges")

    streets = layer[layer.highway.isin(["residential", "tertiary", "secondary"])
                    & (layer.car_direction == "both") & layer.name.notna()]
    way = int(streets.osmid.value_counts().index[0])  # the street with the most stretches
    rows = layer[layer.osmid == way].copy()
    rows["access"] = "no"

    nodes, edges, osm_nodes, osm_edges = build_network(
        area, rows, osm_source=extract, return_source_osm=True)
    write_osm(osm_nodes, osm_edges, folder / "before.osm.pbf")
    write_osm(nodes, edges, folder / "after.osm.pbf")
    return (Router.from_pbf(folder / "before.osm.pbf", folder / "before"),
            Router.from_pbf(folder / "after.osm.pbf", folder / "after"), way)


def test_closed_street_is_no_longer_driven_along(closed_street):
    before, after, way = closed_street
    refs, tags = before.ways[way]
    start, end = before.node(refs[0]), before.node(refs[-1])

    changed = [w for w, (_, t) in after.ways.items() if t.get("nf:modified") == "yes"]
    assert changed and all(after.ways[w][1]["access"] == "no" for w in changed)
    assert all(after.ways[w][1]["name"] == tags["name"] for w in changed)

    assert before.route(start, end, "auto", shortest=True).uses(way)
    detour = after.route(start, end, "auto", shortest=True)
    assert detour is None or not (set(detour.way_ids) & {way, *changed})


def test_closing_one_street_leaves_the_rest_of_the_file_alone(closed_street):
    before, after, way = closed_street
    changed = {w for w, (_, t) in after.ways.items() if t.get("nf:modified") == "yes"}

    assert {w: v for w, v in after.ways.items() if w not in changed and w != way} == {
        w: v for w, v in before.ways.items() if w != way}
