"""
Cases found by probing the build with shapes the other tests don't draw
(each was a bug): vertices near a crossing of a bridge, a dead end
beside a crossing, line ends landing a hair from a crossing, a street
that closes on itself, a restriction over a via way.
"""

import pytest
import shapely

from networkforge import osm
from networkforge.export import prepare_osm_data
from tests.integration.grid import (
    DEAD_END_NODES,
    LOOP_NODES,
    LOOP_WAY,
    MOTORWAY_NODES,
    VIA_WAY_RESTRICTION,
    X0,
    Y0,
    grid_with_dead_end,
    grid_with_loop,
    grid_with_via_way_restriction,
    node_id,
)

ROAD = {"highway": "residential"}
BRIDGE = {**ROAD, "bridge": "yes", "layer": "1"}


def at(x: float, y: float) -> tuple[float, float]:
    """Metres from the grid's corner (node 1)."""
    return (X0 + x, Y0 + y)


def nodes_of(edges, part: int) -> set[int]:
    """The nodes of custom line number `part` (the order they were given in)."""
    rows = edges[edges["nf_part"] == part]
    return {*rows.u.astype(int), *rows.v.astype(int)}


def osm_nodes_of(edges) -> set[int]:
    rows = edges[edges["custom"] != "yes"]
    return {*rows.u.astype(int), *rows.v.astype(int)}


# ---------------------------------------------------------------------
# A bridge joins nothing between its ends, wherever the vertices are
# ---------------------------------------------------------------------

# A road north from Row 0 Street, and a line crossing it 30 m up, from
# Column 0 Avenue to Column 1 Avenue.
ROAD_NORTH = [at(50, 0), at(50, 90)]


@pytest.mark.parametrize("over", [
    [at(0, 30), at(50, 30), at(100, 30)],      # a vertex exactly over the road
    [at(0, 30), at(50.6, 30), at(100, 30)],    # ... or beside it, within snap_tolerance
])
@pytest.mark.parametrize("tags", [
    BRIDGE, {**ROAD, "tunnel": "yes", "layer": "-1"}, {**ROAD, "layer": "1"}, {"route": "ferry"},
], ids=["bridge", "tunnel", "layer", "ferry"])
def test_vertex_of_a_bridge_near_the_road_below_does_not_join_it(build, over, tags):
    edges = build([(ROAD_NORTH, ROAD), (over, tags)]).edges
    assert not nodes_of(edges, 0) & nodes_of(edges, 1)


def test_vertex_of_a_road_under_a_bridge_does_not_join_it(build):
    road = [at(50, 0), at(50, 30), at(50, 90)]
    edges = build([(road, ROAD), ([at(0, 30), at(100, 30)], BRIDGE)]).edges
    assert not nodes_of(edges, 0) & nodes_of(edges, 1)


def test_road_ending_under_a_bridge_joins_it(build):
    """An END joins what it reaches, as a slip road joins a motorway."""
    edges = build([([at(50, 0), at(50, 30)], ROAD), ([at(0, 30), at(100, 30)], BRIDGE)]).edges
    assert len(nodes_of(edges, 0) & nodes_of(edges, 1)) == 1


def test_vertex_of_a_bridge_on_a_junction_does_not_join_it(build):
    """Drawn with a vertex snapped to node 7, the junction it passes over."""
    bridge = [at(0, 150), at(100, 100), at(150, 0)]
    result = build([(bridge, BRIDGE)])
    edges = result.edges
    joined = nodes_of(edges, 0) & osm_nodes_of(edges)

    assert node_id(1, 1) not in joined
    assert len(joined) == 2  # its two ends, mid-block
    # The vertex is still there, a node of the bridge alone.
    own = result.nodes.geometry.loc[list(nodes_of(edges, 0) - joined)]
    assert (own.distance(shapely.Point(at(100, 100))) < 0.01).sum() == 1


def test_middle_vertex_on_a_motorway_node_does_not_join_the_motorway(build):
    road = [at(400, 50), at(500, 0), at(550, -50)]
    edges = build([(road, ROAD)]).edges
    assert not nodes_of(edges, 0) & set(MOTORWAY_NODES)


def test_line_crossing_itself_on_a_street_does_not_join_a_tunnel_there(build):
    """
    The path crosses itself 0.4 m from Row 2 Street, so the crossing is
    moved onto the street - where a tunnel passes within reach. A crossing
    is not a line end (only ends join a tunnel), moved or not.
    """
    path = [at(41, 34), at(0, 100), at(399.6, 299), at(99.4, 200.4), at(401, 200.4), at(325, 201)]
    tunnel = [at(100, 100), at(200.3, 200), at(500.5, 228), at(500, 228.1), at(100, 100)]
    edges = build([(path, {"highway": "cycleway"}),
                   (tunnel, {**ROAD, "tunnel": "yes", "layer": "-1"})]).edges

    under = edges[edges["tunnel"] == "yes"]
    path_nodes = nodes_of(edges[edges["highway"] == "cycleway"].assign(nf_part=0), 0)
    assert {*under.u.astype(int), *under.v.astype(int)} & path_nodes <= {node_id(1, 1)}


# ---------------------------------------------------------------------
# A dead end just short of the street a line crosses
# ---------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    [at(120, 130), at(180, 70)],     # crosses Row 1 Street beside the dead end
    [at(150, 130), at(150, 100)],    # ends on Row 1 Street beside it
], ids=["crossing", "ending"])
def test_line_meeting_a_street_beside_a_dead_end_joins_the_street(build, monkeypatch, line):
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_dead_end())
    result = build([(line, ROAD), ([at(100, 200), line[0]], ROAD)])
    edges = result.edges

    row_1 = edges[(edges["name"] == "Row 1 Street")]
    on_row_1 = {*row_1.u.astype(int), *row_1.v.astype(int)}
    shared = nodes_of(edges, 0) & on_row_1
    assert len(shared) == 1
    assert result.nodes.geometry.loc[shared.pop()].distance(shapely.Point(at(150, 100))) < 0.01
    assert not on_row_1 & set(DEAD_END_NODES)  # the street itself is as it was


# ---------------------------------------------------------------------
# One node per place
# ---------------------------------------------------------------------

def no_two_nodes_in_one_place(result) -> None:
    assert (result.edges.geometry.length > 1e-6).all()
    points = result.nodes.geometry.to_numpy()
    a, b = shapely.STRtree(points).query(points, predicate="dwithin", distance=1e-6)
    assert (a == b).all()


def test_end_pulled_onto_a_street_is_one_node(build):
    """
    The end, 1 m short of Row 4 Street, is moved onto it - and GEOS then
    works out where line and street cross 6e-11 m from that end. (About
    one such line in ten did this.)
    """
    no_two_nodes_in_one_place(build([([at(225, 1.2), at(340.3037682733848, 399.01)], ROAD)]))


def test_two_roads_crossing_each_other_on_a_street_make_one_junction(build):
    """Three crossings worked out separately, each a hair from the others."""
    result = build([([at(30, -20), at(70, 20)], ROAD), ([at(30, 20), at(70, -20)], ROAD)])
    no_two_nodes_in_one_place(result)
    edges = result.edges
    junction = nodes_of(edges, 0) & nodes_of(edges, 1)
    assert len(junction) == 1 and junction <= osm_nodes_of(edges)


# ---------------------------------------------------------------------
# A way that closes on itself
# ---------------------------------------------------------------------

AROUND_THE_LOOP = [at(100, 0), at(70, -60), at(130, -60), at(100, 0)]
N2, (N601, N602) = node_id(0, 1), LOOP_NODES


def loop_edges(edges) -> set[tuple[int, int]]:
    loop = {N2, N601, N602}
    return {(int(u), int(v)) for u, v in zip(edges.u, edges.v, strict=True) if {u, v} <= loop}


@pytest.mark.parametrize("line, travel, written", [
    (AROUND_THE_LOOP, {(N2, N601), (N601, N602), (N602, N2)}, "yes"),
    (AROUND_THE_LOOP[::-1], {(N2, N602), (N602, N601), (N601, N2)}, "-1"),
], ids=["as the way is drawn", "the other way round"])
def test_loop_made_one_way_follows_the_direction_drawn(build, monkeypatch, line, travel, written):
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_loop())
    result = build([(line, {"osm_id": LOOP_WAY, "oneway": "yes"})])

    assert loop_edges(result.edges) == travel
    ways = [(refs, tags) for _, refs, tags in prepare_osm_data(result.nodes, result.edges).ways
            if N601 in refs]
    assert [(refs, tags["oneway"]) for refs, tags in ways] == [([N2, N601, N602, N2], written)]


def test_closing_step_of_a_loop_made_one_way(build, monkeypatch):
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_loop())
    result = build([([at(100, 0), at(130, -60)], {"osm_id": LOOP_WAY, "oneway": "yes"})])
    assert loop_edges(result.edges) == {
        (N2, N601), (N601, N2), (N601, N602), (N602, N601), (N2, N602)}


# ---------------------------------------------------------------------
# A turn restriction over a via way keeps up with changes to its ways
# ---------------------------------------------------------------------

def via_way_restriction(result) -> list[tuple[str, list[int]]] | None:
    """(role, nodes of the way) of each member, or None if it was left out."""
    data = prepare_osm_data(result.nodes, result.edges)
    ways = {way_id: refs for way_id, refs, _ in data.ways}
    members = next((members for relation_id, members, _ in data.relations
                    if relation_id == VIA_WAY_RESTRICTION), None)
    return members and [(role, ways[ref]) for _, ref, role in members]


@pytest.fixture
def via_way_grid(monkeypatch):
    monkeypatch.setattr(
        osm, "_download_elements", lambda polygon, kind: grid_with_via_way_restriction())


SLOW = {"maxspeed": "10 mph"}


def test_via_way_restriction_is_kept_as_it_is(build, via_way_grid):
    assert via_way_restriction(build([([at(0, 0), at(50, 50)], ROAD)])) == [
        ("from", [13, 18]), ("via", [18, 19, 20]), ("to", [10, 15, 20, 25])]


def test_via_way_restriction_survives_a_change_further_along_its_to_way(build, via_way_grid):
    """Column 4 Avenue is one way, 10 to 25; the change is two blocks from the junction."""
    result = build([([at(400, 100), at(400, 200)], {"osm_id": 18, **SLOW})])
    assert via_way_restriction(result) == [
        ("from", [13, 18]), ("via", [18, 19, 20]), ("to", [15, 20, 25])]


def test_via_way_changed_in_part_is_two_via_ways_in_order(build, via_way_grid):
    result = build([([at(300, 300), at(400, 300)], {"osm_id": 7, **SLOW})])
    assert via_way_restriction(result) == [
        ("from", [13, 18]), ("via", [18, 19]), ("via", [19, 20]), ("to", [10, 15, 20, 25])]


def test_via_way_restriction_goes_when_part_of_the_via_way_is_removed(build, via_way_grid):
    result = build([([at(300, 300), at(400, 300)], {"osm_id": 7, "remove": "yes"})])
    assert via_way_restriction(result) is None
