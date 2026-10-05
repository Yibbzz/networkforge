"""
Property test: for any custom lines Hypothesis draws on the grid,
Valhalla must build a graph from the after file, and measure every trip
the same as NetworkForge's own routing does (OSMnx on the same file,
whose before / after rules tests/integration/test_properties.py checks).

So whatever shape a line has - crossing streets anywhere, ending near
junctions, bridges, bus gates - the network a router sees is the
network NetworkForge reports.

More examples: HYPOTHESIS_PROFILE=thorough uv run pytest tests/valhalla/test_random_lines.py
"""

import math
import shutil
import tempfile
from pathlib import Path

import networkx as nx
import osmium
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from networkforge.modes import load_graph
from tests.integration.grid import GRID_NODES, X0, Y0
from tests.integration.test_properties import custom_feature
from tests.valhalla.conftest import build_scenario
from tests.valhalla.test_before_and_after import PLAIN_GRID

MODES = [("drive", "auto"), ("bike", "bicycle"), ("walk", "pedestrian")]

# Valhalla stores each edge's length in whole metres, so a trip over
# several edges can be a few metres out.
TOLERANCE = {"rel": 0.005, "abs": 5}

# Each example builds two networks and a Valhalla graph (about 2 s).
EXAMPLES = max(5, settings.default.max_examples // 4)


def own_distances(pbf: Path, folder: Path, mode: str) -> dict:
    """Shortest distances between grid junctions by NetworkForge's own routing."""
    xml = folder / f"{mode}.osm"
    writer = osmium.SimpleWriter(str(xml))
    for obj in osmium.FileProcessor(str(pbf)):
        writer.add(obj)
    writer.close()
    return dict(nx.all_pairs_dijkstra_path_length(load_graph(str(xml), mode), weight="length"))


# Found by the nightly run: a road from node 2 out past the grid's corner
# and back to node 1. Valhalla's matrix says 229 m from node 1 to node 2;
# its route search (and NetworkForge) say 100 m, along Row 0 Street.
MATRIX_DISAGREES = [([(X0 + 100, Y0), (X0 - 50, Y0 - 50), (X0, Y0)],
                     {"highway": "primary", "maxspeed": "30 mph", "oneway": "no"})]


# Also from the nightly run: a loop from node 1 that stops 1.5 m short
# of it. Valhalla moves a location onto a graph node up to 5 m away, and
# moved a trip starting on node 1 to the loop's dead end.
STOPS_NEAR_A_JUNCTION = [([(X0, Y0), (X0, Y0), (X0 - 50, Y0 - 50), (X0 - 1.5, Y0)],
                          {"highway": "primary", "maxspeed": "30 mph", "oneway": "no"})]


# And a road drawn along Row 0 Street to node 3 and back. Going out it
# lies on the street and joins it at node 2; coming back it runs 15 cm
# beside the street, passing node 2 without crossing anything, so (by the
# rules) without joining it. A trip "from node 2" is then ambiguous:
# Valhalla starts it on the road. Such junctions are left out.
LAID_OVER_A_STREET = [([(X0, Y0), (X0 + 200, Y0), (X0, Y0 + 0.3)],
                       {"highway": "residential", "maxspeed": "20 mph"})]


# Valhalla moves a location onto a graph node this close (its default
# node_snap_tolerance, metres), and onto an edge passing this close.
NODE_SNAP_M, EDGE_NEAR_M = 5.0, 0.5


def ambiguous_junctions(built) -> set[int]:
    """
    Grid junctions where "start here" is unclear to a router: another
    node within Valhalla's node snapping distance, or an edge passing
    within half a metre without meeting the junction. Valhalla may start
    the trip there instead, so such trips are not a fair comparison.
    """
    edges, nodes = built.edges, built.nodes.geometry
    unclear = set()
    for node in GRID_NODES:
        point = nodes.loc[node]
        others = nodes.drop(node)
        near = edges[edges.geometry.distance(point) < EDGE_NEAR_M]
        if (others.distance(point) < NODE_SNAP_M).any() or (
                (near.u != node) & (near.v != node)).any():
            unclear.add(node)
    return unclear


@settings(max_examples=EXAMPLES, deadline=None)
@example(features=MATRIX_DISAGREES)
@example(features=LAID_OVER_A_STREET)
@example(features=STOPS_NEAR_A_JUNCTION)
@given(features=st.lists(custom_feature(), min_size=1, max_size=2))
def test_valhalla_agrees_with_networkforge_for_any_custom_lines(features):
    folder = Path(tempfile.mkdtemp(prefix="nf-valhalla-"))
    try:
        built = build_scenario(features, folder, elements=PLAIN_GRID)
        points = [built.after.node(node) for node in GRID_NODES]
        unclear = ambiguous_junctions(built)

        for mode, costing in MODES:
            own = own_distances(built.after.pbf, folder, mode)

            def expected(i, j, own=own):
                return own.get(GRID_NODES[i], {}).get(GRID_NODES[j], math.inf)

            def agrees(i, j, length, expected=expected):
                return length == pytest.approx(expected(i, j), **TOLERANCE)

            valhalla = built.after.distances(points, costing, agrees_with=agrees, shortest=True)
            for i, origin in enumerate(GRID_NODES):
                for j, destination in enumerate(GRID_NODES):
                    if origin in unclear or destination in unclear:
                        continue
                    found = valhalla[i][j]
                    assert agrees(i, j, found), (
                        f"{mode} {origin}->{destination}: Valhalla {found:.0f} m, "
                        f"NetworkForge {expected(i, j):.0f} m")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
