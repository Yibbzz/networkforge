"""
Hand-built OSM street grid used in place of the Overpass download.

      row 4   21 x 22 -- 23 -- 24 -- 25        x = bus gate (motor_vehicle=no)
              |     |     |     |     |
      row 3   16 a 17 -- 18 -- 19 -- 20        a = access=no + foot=yes (walk only)
              |     |     |    /|     |        / = DIAGONAL custom line
      row 2   11 -- 12 -- 13 -- 14 -l 15           (13 -> 19)
      ========|=====|=====f=====|=====|====    l = cycle lane, surface, oneway:bicycle
      row 1    6 --  7 --  8 --  9 -- 10       = = ROAD_ACROSS custom line
              |     |     |     |     c        f = footway (8-13)
      row 0    1 --  2 --  B --  4 --  5       c = cycleway (5-10)
                                               B = bollard (barrier node)
                                                  motorway (100 -> 101), x=500 m,
                                                  joined by a slip road (5 -> 100)

100 m blocks. All other ways are residential, 30 mph, two-way. Node
ids are deliberately small (like the oldest real OSM nodes) to catch
id collisions with the nodes the build creates.

Each row is "Row r Street" and each column "Column c Avenue", drawn as
OSM ways several blocks long (a new way wherever the tags change), and
one turn restriction (NO_LEFT_TURN) - so the OSM data has the shapes
real data has: long ways, names and relations.
"""

import geopandas as gpd
import networkx as nx
from osmnx.graph import _create_graph
from pyproj import Transformer
from shapely.geometry import box

from networkforge.modes import keep_mode_tags
from tests.helpers import WEIGHT

UTM = "EPSG:32630"
X0, Y0 = 500_000.0, 6_200_000.0
SPACING = 100.0
N = 5


def node_id(row: int, col: int) -> int:
    return 1 + row * N + col


GRID_NODES = [node_id(r, c) for r in range(N) for c in range(N)]
MOTORWAY_NODES = (100, 101)
SLIP_ROAD = (node_id(0, 4), MOTORWAY_NODES[0])

RESIDENTIAL = {"highway": "residential", "maxspeed": "30 mph"}
FOOTWAY = frozenset({node_id(1, 2), node_id(2, 2)})
CYCLEWAY = frozenset({node_id(0, 4), node_id(1, 4)})
BUS_GATE = frozenset({node_id(4, 0), node_id(4, 1)})
NO_ACCESS = frozenset({node_id(3, 0), node_id(3, 1)})
CYCLE_LANE_STREET = frozenset({node_id(2, 3), node_id(2, 4)})
CYCLE_LANE_TAGS = {"cycleway": "lane", "oneway:bicycle": "no", "surface": "asphalt"}
SPECIAL_WAYS = {
    FOOTWAY: {"highway": "footway"},
    CYCLEWAY: {"highway": "cycleway"},
    BUS_GATE: {**RESIDENTIAL, "motor_vehicle": "no"},
    NO_ACCESS: {**RESIDENTIAL, "access": "no", "foot": "yes"},
    CYCLE_LANE_STREET: {**RESIDENTIAL, **CYCLE_LANE_TAGS},
}

BOLLARD_NODE = node_id(0, 2)
NODE_TAGS = {BOLLARD_NODE: {"barrier": "bollard"}}

# Crosses every vertical street between rows 1 and 2 (incl. the footway).
ROAD_ACROSS = [(X0 - 50, Y0 + 150), (X0 + 450, Y0 + 150)]
ROAD_TAGS = {"highway": "primary", "maxspeed": "30 mph", "oneway": "no"}

# Starts 0.5 m from node 13 (inside snap_tolerance), ends exactly on node 19.
DIAGONAL = [(X0 + 200.5, Y0 + 200), (X0 + 300, Y0 + 300)]

# Like ROAD_ACROSS but long enough to cross the motorway at x=500 m.
ROAD_TO_MOTORWAY = [(X0 - 50, Y0 + 150), (X0 + 600, Y0 + 150)]


# no_left_turn: (from segment, via node, to segment). Driving east along
# row 3 you may not turn left (north) at node 18.
NO_LEFT_TURN = ((node_id(3, 1), node_id(3, 2)), node_id(3, 2), (node_id(3, 2), node_id(4, 2)))
RESTRICTION_ID = 9001


def _street_ways(lines: list[tuple[str, list[int]]], breaks: set[int]) -> list[dict]:
    """
    Each line of nodes as OSM ways: one way for as long as the tags stay
    the same (as mappers draw streets), cut at `breaks` (the via node of
    a turn restriction has to be a way end).
    """
    ways = []
    for name, nodes in lines:
        run, run_tags = [nodes[0]], None
        for u, v in zip(nodes, nodes[1:], strict=False):
            tags = SPECIAL_WAYS.get(frozenset({u, v}), RESIDENTIAL)
            if run_tags is not None and (tags != run_tags or u in breaks):
                ways.append((run, {**run_tags, "name": name}))
                run = [u]
            run.append(v)
            run_tags = tags
        ways.append((run, {**run_tags, "name": name}))
    return [{"type": "way", "id": way_id, "nodes": nodes, "tags": tags}
            for way_id, (nodes, tags) in enumerate(ways, start=1)]


def grid_elements() -> list[dict]:
    """The grid above as OSM data, shaped like an Overpass JSON response."""
    to_wgs84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)

    def node(nid, x, y):
        lon, lat = to_wgs84.transform(x, y)
        return {"type": "node", "id": nid, "lon": lon, "lat": lat, "tags": NODE_TAGS.get(nid, {})}

    nodes = [node(node_id(r, c), X0 + c * SPACING, Y0 + r * SPACING)
             for r in range(N) for c in range(N)]
    nodes += [node(MOTORWAY_NODES[0], X0 + 500, Y0), node(MOTORWAY_NODES[1], X0 + 500, Y0 + 400)]

    rows = [(f"Row {r} Street", [node_id(r, c) for c in range(N)]) for r in range(N)]
    columns = [(f"Column {c} Avenue", [node_id(r, c) for r in range(N)]) for c in range(N)]
    (from_u, from_v), via, (to_u, to_v) = NO_LEFT_TURN
    ways = _street_ways(rows + columns, breaks={via})
    ways.append({"type": "way", "id": len(ways) + 1, "nodes": list(MOTORWAY_NODES),
                 "tags": {"highway": "motorway", "maxspeed": "70 mph", "oneway": "yes"}})
    # A slip road joins the motorway to the grid (the tests drive onto it).
    ways.append({"type": "way", "id": len(ways) + 1, "nodes": list(SLIP_ROAD),
                 "tags": {"highway": "motorway_link", "oneway": "yes"}})

    def way_with(u, v):
        return next(w["id"] for w in ways if any(
            {a, b} == {u, v} for a, b in zip(w["nodes"], w["nodes"][1:], strict=False)))

    restriction = {
        "type": "relation", "id": RESTRICTION_ID,
        "tags": {"type": "restriction", "restriction": "no_left_turn"},
        "members": [
            {"type": "way", "ref": way_with(from_u, from_v), "role": "from"},
            {"type": "node", "ref": via, "role": "via"},
            {"type": "way", "ref": way_with(to_u, to_v), "role": "to"},
        ],
    }
    return [*nodes, *ways, restriction]


FERRY_ID, FERRY_NODE = 700, 300


def grid_with_ferry() -> list[dict]:
    """
    grid_elements() plus a foot ferry from node 1 to node 13, by way of
    a node of its own in the middle of a block. No `highway` tag: it is
    not a street, but routers sail it.
    """
    to_wgs84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    lon, lat = to_wgs84.transform(X0 + 130, Y0 + 70)
    return [
        *grid_elements(),
        {"type": "node", "id": FERRY_NODE, "lon": lon, "lat": lat, "tags": {}},
        {"type": "way", "id": FERRY_ID, "nodes": [node_id(0, 0), FERRY_NODE, node_id(2, 2)],
         "tags": {"route": "ferry", "foot": "yes", "motor_vehicle": "no", "name": "Grid Ferry"}},
    ]


ISLAND_NODES = (401, 402, 403)
ISLAND_WAY = 800


def grid_with_island() -> list[dict]:
    """
    grid_elements() plus a street that joins nothing else, 150 m west of
    the grid: another island, or a street whose link to the rest lies
    outside the area. Three nodes going north, "Island Road".
    """
    to_wgs84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    nodes = []
    for number, nid in enumerate(ISLAND_NODES):
        lon, lat = to_wgs84.transform(X0 - 80, Y0 + 100 + number * 100)
        nodes.append({"type": "node", "id": nid, "lon": lon, "lat": lat, "tags": {}})
    return [
        *grid_elements(), *nodes,
        {"type": "way", "id": ISLAND_WAY, "nodes": list(ISLAND_NODES),
         "tags": {**RESIDENTIAL, "name": "Island Road"}},
    ]


TUNNEL_NODES = (501, 502, 503)
TUNNEL_WAY = 810


def grid_with_tunnel() -> list[dict]:
    """
    grid_elements() plus a road tunnel passing under Row 3 Street half
    way between nodes 18 and 19. Its middle node sits half a metre north
    of the street above: under it, not on it (as tunnel and bridge nodes
    often do in real data).
    """
    to_wgs84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    nodes = []
    for nid, y in zip(TUNNEL_NODES, (250, 300.5, 350), strict=True):
        lon, lat = to_wgs84.transform(X0 + 250, Y0 + y)
        nodes.append({"type": "node", "id": nid, "lon": lon, "lat": lat, "tags": {}})
    return [
        *grid_elements(), *nodes,
        {"type": "way", "id": TUNNEL_WAY, "nodes": list(TUNNEL_NODES),
         "tags": {**RESIDENTIAL, "tunnel": "yes", "layer": "-1", "name": "Tunnel Road"}},
    ]


def synthetic_graph() -> nx.MultiDiGraph:
    """The grid as OSMnx builds a graph from it (simplify=False)."""
    keep_mode_tags()
    return _create_graph(
        [{"elements": [e for e in grid_elements() if e["type"] != "relation"]}],
        bidirectional=False,
    )


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
