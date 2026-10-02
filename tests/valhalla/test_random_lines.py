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
from hypothesis import given, settings
from hypothesis import strategies as st

from networkforge.modes import load_graph
from tests.integration.grid import GRID_NODES
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


@settings(max_examples=EXAMPLES, deadline=None)
@given(features=st.lists(custom_feature(), min_size=1, max_size=2))
def test_valhalla_agrees_with_networkforge_for_any_custom_lines(features):
    folder = Path(tempfile.mkdtemp(prefix="nf-valhalla-"))
    try:
        built = build_scenario(features, folder, elements=PLAIN_GRID)
        points = [built.after.node(node) for node in GRID_NODES]

        for mode, costing in MODES:
            own = own_distances(built.after.pbf, folder, mode)
            valhalla = built.after.matrix(points, costing, shortest=True)
            for i, origin in enumerate(GRID_NODES):
                for j, destination in enumerate(GRID_NODES):
                    expected = own.get(origin, {}).get(destination, math.inf)
                    found = math.inf if valhalla[i][j] is None else valhalla[i][j][0]
                    assert found == pytest.approx(expected, **TOLERANCE), (
                        f"{mode} {origin}->{destination}: Valhalla {found:.0f} m, "
                        f"NetworkForge {expected:.0f} m")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
