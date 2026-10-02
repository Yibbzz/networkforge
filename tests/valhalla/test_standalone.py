"""
A network built from lines alone (standalone, no OpenStreetMap) in
Valhalla: the test grid drawn as one line per street must route exactly
like the same grid given as OpenStreetMap data.
"""

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import LineString

from networkforge import build_network, write_osm
from tests.integration.grid import GRID_NODES, UTM
from tests.valhalla.conftest import at, xy
from tests.valhalla.harness import Router
from tests.valhalla.test_before_and_after import PLAIN_GRID, write_grid_osm

COSTINGS = ("auto", "bus", "truck", "bicycle", "pedestrian")


def grid_as_lines() -> gpd.GeoDataFrame:
    """Each way of the grid's OSM data as a line feature, its tags as attributes."""
    to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    position = {e["id"]: to_utm.transform(e["lon"], e["lat"])
                for e in PLAIN_GRID if e["type"] == "node"}
    ways = [e for e in PLAIN_GRID if e["type"] == "way"]
    return gpd.GeoDataFrame(
        [way["tags"] for way in ways],
        geometry=[LineString([position[ref] for ref in way["nodes"]]) for way in ways],
        crs=UTM,
    )


@pytest.fixture(scope="module")
def routers(tmp_path_factory):
    """(Valhalla on the grid as OSM data, Valhalla on the grid built from lines)."""
    folder = tmp_path_factory.mktemp("standalone")
    write_grid_osm(folder / "osm.osm.pbf", PLAIN_GRID)
    nodes, edges = build_network(None, grid_as_lines(), standalone=True)
    write_osm(nodes, edges, folder / "lines.osm.pbf")
    return (Router.from_pbf(folder / "osm.osm.pbf", folder / "osm"),
            Router.from_pbf(folder / "lines.osm.pbf", folder / "lines"))


def test_the_file_has_one_way_per_line_with_its_attributes(routers):
    as_osm, from_lines = routers
    original = sorted((len(refs), sorted(tags.items())) for refs, tags in as_osm.ways.values())
    built = sorted((len(refs), sorted((k, v) for k, v in tags.items() if k != "nf:custom"))
                   for refs, tags in from_lines.ways.values())
    assert built == original


@pytest.mark.parametrize("costing", COSTINGS)
def test_routes_like_the_same_network_in_osm(routers, costing):
    """Every trip between the grid's junctions: the same distance and time."""
    as_osm, from_lines = routers
    points = [as_osm.node(node) for node in GRID_NODES]
    assert from_lines.matrix(points, costing) == as_osm.matrix(points, costing)


def test_rules_in_the_attributes_are_obeyed(routers):
    """The grid's footway, cycleway, bus gate and one-way motorway, now from attributes."""
    _, network = routers

    def metres(start, end, costing):
        route = network.route(start, end, costing, shortest=True)
        return None if route is None else round(route.length_m)

    assert metres(at(2, 1), at(2, 2), "pedestrian") == 100     # the footway
    assert metres(at(2, 1), at(2, 2), "auto") == 300
    assert metres(at(4, 0), at(4, 1), "bicycle") == 100        # the cycleway
    assert metres(at(4, 0), at(4, 1), "auto") == 300
    assert metres(at(0, 4), at(1, 4), "bicycle") == 100        # the bus gate
    assert metres(at(0, 4), at(1, 4), "auto") == 500
    assert metres(at(5, 0), at(5, 4), "auto") == 400           # up the motorway
    assert metres(at(5, 4), at(5, 0), "auto") is None          # not back down it


def test_names_are_in_the_directions(routers):
    _, network = routers
    route = network.route(at(0, 0), at(4, 0), "pedestrian", shortest=True)
    assert route.names == ("Row 0 Street",)


@pytest.mark.parametrize("costing", COSTINGS)
def test_joining_at_vertices_gives_the_same_network_for_clean_data(tmp_path_factory, routers,
                                                                   costing):
    """The grid has a vertex at every junction, so both ways of joining agree."""
    as_osm, _ = routers
    folder = tmp_path_factory.mktemp("vertices")
    nodes, edges = build_network(None, grid_as_lines(), standalone=True, join_at="vertices")
    write_osm(nodes, edges, folder / "lines.osm.pbf")
    by_vertices = Router.from_pbf(folder / "lines.osm.pbf", folder / "tiles")

    points = [as_osm.node(node) for node in GRID_NODES]
    assert by_vertices.matrix(points, costing) == as_osm.matrix(points, costing)


def test_flyover_without_a_bridge_tag_only_stays_apart_when_joining_at_vertices(tmp_path):
    """
    Two roads cross with no vertex in common and nothing saying
    "bridge": joined by default, kept apart with join_at="vertices".
    """
    lines = gpd.GeoDataFrame(
        [{"highway": "residential"}] * 3,
        geometry=[LineString([xy(0, 1), xy(2, 1)]), LineString([xy(1, 0), xy(1, 2)]),
                  LineString([xy(0, 1), xy(0, 0), xy(1, 0)])], crs=UTM)

    def metres(join_at):
        nodes, edges = build_network(None, lines, standalone=True, join_at=join_at)
        folder = tmp_path / join_at
        folder.mkdir()
        write_osm(nodes, edges, folder / "n.osm.pbf")
        router = Router.from_pbf(folder / "n.osm.pbf", folder / "tiles")
        return round(router.route(at(2, 1), at(1, 2), "auto", shortest=True).length_m)

    assert metres("crossings") == 200          # turn at the crossing
    assert metres("vertices") == 600           # all the way round


def test_gap_between_lines_is_a_gap_for_the_router(tmp_path):
    """Two streets drawn 2 m apart are not joined; within a metre they are."""
    def network(gap):
        lines = gpd.GeoDataFrame(
            [{"highway": "residential"}] * 2,
            geometry=[LineString([xy(0, 0), xy(1, 0)]),
                      LineString([(xy(1, 0)[0] + gap, xy(1, 0)[1]), xy(2, 0)])], crs=UTM)
        nodes, edges = build_network(None, lines, standalone=True)
        folder = tmp_path / f"gap-{gap}"
        folder.mkdir()
        write_osm(nodes, edges, folder / "n.osm.pbf")
        return Router.from_pbf(folder / "n.osm.pbf", folder / "tiles")

    assert network(0.5).route(at(0, 0), at(2, 0), "auto").length_m == pytest.approx(200, abs=2)
    assert network(2.0).route(at(0, 0), at(2, 0), "auto") is None
