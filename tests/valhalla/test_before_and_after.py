"""
The two files as a pair, across the whole grid rather than one route:

- "before" must route exactly like the OpenStreetMap data it was made
  from (written here by pyosmium, without NetworkForge);
- "after" must differ from "before" only where a custom line helps;
- Valhalla on the PBF and NetworkForge's own routing (OSMnx, the same
  rules as the GeoPackage columns) must agree on every trip.
"""

import math

import networkx as nx
import osmium
import pytest

from networkforge import write_osm
from networkforge.modes import load_graph
from tests.integration.grid import GRID_NODES, grid_elements
from tests.valhalla.conftest import xy
from tests.valhalla.harness import Router

COSTINGS = ("auto", "bus", "truck", "bicycle", "pedestrian")
ROAD = {"highway": "residential", "maxspeed": "30 mph"}
SHORTCUT = [xy(1, 1), xy(2, 2)]


def grid_points(router):
    return [router.node(node) for node in GRID_NODES]


def write_grid_osm(path) -> None:
    """The grid's OSM data as a file, written by pyosmium (not by NetworkForge)."""
    writer = osmium.SimpleWriter(str(path))
    for element in grid_elements():
        if element["type"] == "node":
            writer.add_node(osmium.osm.mutable.Node(
                id=element["id"], location=(element["lon"], element["lat"]),
                tags=element["tags"]))
        elif element["type"] == "way":
            writer.add_way(osmium.osm.mutable.Way(
                id=element["id"], nodes=element["nodes"], tags=element["tags"]))
        else:
            writer.add_relation(osmium.osm.mutable.Relation(
                id=element["id"], tags=element["tags"],
                members=[(m["type"][0], m["ref"], m["role"]) for m in element["members"]]))
    writer.close()


@pytest.fixture(scope="module")
def osm_itself(tmp_path_factory):
    """Valhalla's graph of the grid's OSM data, written without NetworkForge."""
    folder = tmp_path_factory.mktemp("osm-itself")
    write_grid_osm(folder / "osm.osm.pbf")
    return Router.from_pbf(folder / "osm.osm.pbf", folder / "tiles")


@pytest.mark.parametrize("costing", COSTINGS)
def test_before_routes_exactly_like_the_osm_data(scenario, osm_itself, costing):
    """Every trip between grid junctions: same distance and same time."""
    before = scenario([(SHORTCUT, ROAD)]).before
    assert before.matrix(grid_points(before), costing) == osm_itself.matrix(
        grid_points(osm_itself), costing)


@pytest.mark.parametrize("costing", ["auto", "bus", "truck", "bicycle"])
@pytest.mark.parametrize("footpath", [
    SHORTCUT,                          # junction to junction
    [xy(0.5, -0.3), xy(0.5, 4.3)],     # across five streets mid-block: new nodes on each
], ids=["between junctions", "across streets"])
def test_a_footpath_leaves_vehicle_trips_alone(scenario, footpath, costing):
    """
    Same distances for every trip. Times are not compared: Valhalla
    slows vehicles a second or two at every junction, and a path
    joining a street makes a junction (see docs/network-analyst.md).
    """
    built = scenario([(footpath, {"highway": "footway"})])
    points = grid_points(built.before)
    before = built.before.matrix(points, costing, shortest=True)
    after = built.after.matrix(points, costing, shortest=True)

    for row_before, row_after in zip(before, after, strict=True):
        for cell_before, cell_after in zip(row_before, row_after, strict=True):
            assert (cell_before is None) == (cell_after is None)
            if cell_before is not None:
                assert cell_after[0] == pytest.approx(cell_before[0], abs=1)


@pytest.mark.parametrize("costing", COSTINGS)
def test_a_new_street_never_makes_a_trip_longer(scenario, costing):
    built = scenario([(SHORTCUT, ROAD)])
    points = grid_points(built.before)
    before = built.before.matrix(points, costing, shortest=True)
    after = built.after.matrix(points, costing, shortest=True)

    shorter = 0
    for row_before, row_after in zip(before, after, strict=True):
        for cell_before, cell_after in zip(row_before, row_after, strict=True):
            if cell_before is None:
                continue
            assert cell_after is not None and cell_after[0] <= cell_before[0] + 1
            shorter += cell_after[0] < cell_before[0] - 1
    assert shorter > 0


# ---------------------------------------------------------------------
# Valhalla and NetworkForge's own routing agree
# ---------------------------------------------------------------------

# OSMnx routing (and so the GeoPackage columns) knows nothing of
# barriers on nodes or turn restrictions; Valhalla obeys both. Compare
# on the grid without them, so that any other disagreement shows.
PLAIN_GRID = [
    {**element, "tags": {}} if element["type"] == "node" else element
    for element in grid_elements() if element["type"] != "relation"
]

FEATURES = [
    (SHORTCUT, {**ROAD, "oneway": "yes"}),
    ([xy(0.5, -0.3), xy(0.5, 4.3)], {"highway": "cycleway"}),
    ([xy(3, 3), xy(4, 4)], {"highway": "footway"}),
    ([xy(2.5, 2.5), xy(2.5, 4)], {"highway": "primary", "motor_vehicle": "no"}),
]


@pytest.mark.parametrize("mode, costing", [
    ("drive", "auto"), ("bike", "bicycle"), ("walk", "pedestrian"),
])
@pytest.mark.parametrize("which", ["before", "after"])
def test_valhalla_and_networkforge_measure_the_same_trips(scenario, tmp_path, which, mode, costing):
    built = scenario(FEATURES, elements=PLAIN_GRID)
    router = getattr(built, which)

    # OSMnx reads XML only: convert the PBF Valhalla was given.
    xml = tmp_path / "network.osm"
    writer = osmium.SimpleWriter(str(xml))
    for obj in osmium.FileProcessor(str(router.pbf)):
        writer.add(obj)
    writer.close()
    graph = load_graph(str(xml), mode)
    own = dict(nx.all_pairs_dijkstra_path_length(graph, weight="length"))

    valhalla = router.matrix(grid_points(router), costing, shortest=True)
    for i, origin in enumerate(GRID_NODES):
        for j, destination in enumerate(GRID_NODES):
            expected = own.get(origin, {}).get(destination, math.inf)
            cell = valhalla[i][j]
            found = math.inf if cell is None else cell[0]
            assert found == pytest.approx(expected, rel=0.002, abs=2), (
                f"{which} {mode} {origin}->{destination}: "
                f"Valhalla {found:.0f} m, NetworkForge {expected:.0f} m")


def test_xml_and_pbf_give_valhalla_the_same_graph(scenario, tmp_path):
    """Valhalla reads PBF; the .osm export must hold the same network."""
    built = scenario([(SHORTCUT, ROAD)])
    write_osm(built.nodes, built.edges, tmp_path / "after.osm")

    from_pbf = osmium.SimpleWriter(str(tmp_path / "from_xml.osm.pbf"))
    for obj in osmium.FileProcessor(str(tmp_path / "after.osm")):
        from_pbf.add(obj)
    from_pbf.close()
    round_trip = Router.from_pbf(tmp_path / "from_xml.osm.pbf", tmp_path / "tiles")

    points = grid_points(built.after)
    for costing in COSTINGS:
        assert round_trip.matrix(points, costing) == built.after.matrix(points, costing)
