"""
Hand-built OSM street grid used in place of the Overpass download.

      row 4   21 x 22 -- 23 -- 24 -- 25        x = bus gate (motor_vehicle=no)
              |     |     |     |     |
      row 3   16 -- 17 -- 18 -- 19 -- 20
              |     |     |    /|     |        / = DIAGONAL custom line
      row 2   11 -- 12 -- 13 -- 14 -- 15           (13 -> 19)
      ========|=====|=====f=====|=====|====    = = ROAD_ACROSS custom line
      row 1    6 --  7 --  8 --  9 -- 10       f = footway (8-13)
              |     |     |     |     c        c = cycleway (5-10)
      row 0    1 --  2 --  3 --  4 --  5          motorway (100 -> 101), x=500 m

100 m blocks. All other ways are residential, 30 mph, two-way. Node
ids are deliberately small (like the oldest real OSM nodes) to catch
id collisions with the nodes the build creates.
"""

import itertools

import geopandas as gpd
import networkx as nx
from pyproj import Transformer
from shapely.geometry import box

from tests.helpers import WEIGHT

UTM = "EPSG:32630"
X0, Y0 = 500_000.0, 6_200_000.0
SPACING = 100.0
N = 5


def node_id(row: int, col: int) -> int:
    return 1 + row * N + col


GRID_NODES = [node_id(r, c) for r in range(N) for c in range(N)]
MOTORWAY_NODES = (100, 101)

RESIDENTIAL = {"highway": "residential", "maxspeed": "30 mph"}
FOOTWAY = frozenset({node_id(1, 2), node_id(2, 2)})
CYCLEWAY = frozenset({node_id(0, 4), node_id(1, 4)})
BUS_GATE = frozenset({node_id(4, 0), node_id(4, 1)})
SPECIAL_WAYS = {
    FOOTWAY: {"highway": "footway"},
    CYCLEWAY: {"highway": "cycleway"},
    BUS_GATE: {**RESIDENTIAL, "motor_vehicle": "no"},
}

# Crosses every vertical street between rows 1 and 2 (incl. the footway).
ROAD_ACROSS = [(X0 - 50, Y0 + 150), (X0 + 450, Y0 + 150)]
ROAD_TAGS = {"highway": "primary", "maxspeed": "30 mph", "oneway": "no"}

# Starts 0.5 m from node 13 (inside snap_tolerance), ends exactly on node 19.
DIAGONAL = [(X0 + 200.5, Y0 + 200), (X0 + 300, Y0 + 300)]

# Like ROAD_ACROSS but long enough to cross the motorway at x=500 m.
ROAD_TO_MOTORWAY = [(X0 - 50, Y0 + 150), (X0 + 600, Y0 + 150)]


def synthetic_graph() -> nx.MultiDiGraph:
    """The grid above, shaped like ox.graph_from_bbox(simplify=False) output."""
    to_wgs84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    utm_xy = {}

    def add_node(nid, x, y):
        lon, lat = to_wgs84.transform(x, y)
        graph.add_node(nid, x=lon, y=lat)
        utm_xy[nid] = (x, y)

    way_ids = itertools.count(1)

    def add_way(u, v, tags, oneway=False):
        (x1, y1), (x2, y2) = utm_xy[u], utm_xy[v]
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        osmid = next(way_ids)
        directions = [(u, v, False)] if oneway else [(u, v, False), (v, u, True)]
        for a, b, reversed_ in directions:
            graph.add_edge(a, b, osmid=osmid, length=length,
                           oneway=oneway, reversed=reversed_, **tags)

    for r in range(N):
        for c in range(N):
            add_node(node_id(r, c), X0 + c * SPACING, Y0 + r * SPACING)

    for r in range(N):
        for c in range(N):
            for r2, c2 in ((r, c + 1), (r + 1, c)):
                if r2 < N and c2 < N:
                    u, v = node_id(r, c), node_id(r2, c2)
                    add_way(u, v, SPECIAL_WAYS.get(frozenset({u, v}), RESIDENTIAL))

    add_node(MOTORWAY_NODES[0], X0 + 500, Y0)
    add_node(MOTORWAY_NODES[1], X0 + 500, Y0 + 400)
    add_way(*MOTORWAY_NODES, {"highway": "motorway", "maxspeed": "70 mph"}, oneway=True)

    return graph


BBOX = gpd.GeoDataFrame(
    geometry=[box(X0 - 100, Y0 - 100, X0 + 600, Y0 + 500)], crs=UTM
)


def all_pair_costs(graph, mode):
    """{(origin, destination): cost} between every pair of grid nodes."""
    lengths = dict(nx.all_pairs_dijkstra_path_length(graph, weight=WEIGHT[mode]))
    return {
        (a, b): lengths.get(a, {}).get(b, float("inf"))
        for a in GRID_NODES for b in GRID_NODES if a != b
    }
