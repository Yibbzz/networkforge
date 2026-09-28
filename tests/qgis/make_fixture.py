"""
Build the GeoPackage that tests/qgis/check_with_qgis.py checks in real QGIS.

Run with: uv run python -m tests.qgis.make_fixture OUT_DIR

Writes OUT_DIR/network.gpkg (the synthetic grid plus two custom roads)
and OUT_DIR/expected.json: for each routing case, the start and end
points and the route cost QGIS must find, computed from the layer's
analysis columns the way QGIS reads them (tests/helpers.qgis_graph).
tests/integration/test_gpkg.py separately checks that reading equals
the engine's own routing.
"""

import json
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString

from networkforge import build_network, write_gpkg, write_osm
from networkforge.export import analysis_edges
from tests.helpers import qgis_graph
from tests.integration.grid import (
    BBOX,
    DIAGONAL,
    ROAD_ACROSS,
    ROAD_TAGS,
    UTM,
    node_id,
    synthetic_graph,
)

# ROAD_ACROSS: two-way primary. DIAGONAL (node 13 -> 19): one-way for cars,
# both ways for bikes (contraflow) - so direction fields must be honoured.
FEATURES = [
    (ROAD_ACROSS, ROAD_TAGS),
    (DIAGONAL, {**ROAD_TAGS, "oneway": "yes", "oneway:bicycle": "no"}),
]

# (from, to) grid nodes. 13->19 / 19->13 differ for cars (one-way diagonal).
PAIRS = [
    (node_id(2, 2), node_id(3, 3)),
    (node_id(3, 3), node_id(2, 2)),
    (node_id(0, 0), node_id(4, 4)),
    (node_id(4, 4), node_id(0, 0)),
    (node_id(1, 0), node_id(1, 4)),
    (node_id(4, 0), node_id(0, 4)),
]

MODES = {"drive": "car", "bike": "bike", "walk": "walk"}


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        grid_pbf = Path(tmp) / "grid.osm.pbf"
        grid_nodes, grid_edges = ox.graph_to_gdfs(synthetic_graph())
        write_osm(grid_nodes, grid_edges.reset_index(), grid_pbf)

        custom = gpd.GeoDataFrame([tags for _, tags in FEATURES],
                                  geometry=[LineString(c) for c, _ in FEATURES], crs=UTM)
        nodes, edges = build_network(BBOX, custom, osm_source=grid_pbf)

    write_gpkg(nodes, edges, out_dir / "network.gpkg")

    # QGIS measures the geometry exactly; the length_m / car_minutes
    # columns are rounded for display, so expect exact values instead.
    layer = analysis_edges(nodes, edges)
    layer["length_m"] = layer.geometry.length
    layer["car_minutes"] = layer["length_m"] / 1000 / layer["speed_kph"] * 60

    cases = []
    for mode, column in MODES.items():
        graph = qgis_graph(layer, mode)
        for start, end in PAIRS:
            cases.append({
                "name": f"{column} shortest {start}->{end}",
                "mode": column, "strategy": "shortest",
                "start": list(nodes.geometry.loc[start].coords[0]),
                "end": list(nodes.geometry.loc[end].coords[0]),
                "expected": nx.shortest_path_length(graph, start, end, weight="length"),
            })
        if mode == "drive":
            for start, end in PAIRS[:2]:
                cases.append({
                    "name": f"car fastest {start}->{end}",
                    "mode": "car", "strategy": "fastest",
                    "start": list(nodes.geometry.loc[start].coords[0]),
                    "end": list(nodes.geometry.loc[end].coords[0]),
                    # QGIS reports fastest-path cost in hours.
                    "expected": nx.shortest_path_length(graph, start, end, weight="minutes") / 60,
                })

    expected = {"crs": nodes.crs.to_string(), "cases": cases}
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=2))
    print(f"Wrote {out_dir / 'network.gpkg'} and {len(cases)} cases to {out_dir / 'expected.json'}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "qgis-fixture"))
