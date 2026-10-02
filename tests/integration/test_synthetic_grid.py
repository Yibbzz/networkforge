"""
Offline integration tests on a hand-built OSM network (see grid.py).

Run with: uv run pytest tests/integration/test_synthetic_grid.py -v

Every integration rule is checked exactly: junctions, snapping, node
ids, access tags, mode isolation and before/after routing.
"""

import pytest
from shapely.geometry import Point

from networkforge import osm
from networkforge.modes import usable_modes
from networkforge.validation import (
    assert_all_custom_edges_are_connected,
    disconnected_custom_edges,
)
from tests.helpers import (
    ROUTING_MODES,
    assert_valid_osm_xml,
    custom_pairs,
    read_osm_xml,
    route_cost,
)
from tests.integration.grid import (
    BUS_GATE,
    DIAGONAL,
    FOOTWAY,
    ISLAND_NODES,
    ISLAND_WAY,
    ROAD_ACROSS,
    ROAD_TAGS,
    ROAD_TO_MOTORWAY,
    TUNNEL_NODES,
    TUNNEL_WAY,
    X0,
    Y0,
    N,
    all_pair_costs,
    grid_with_island,
    grid_with_tunnel,
    node_id,
)


def osm_edge_nodes(edges, highway):
    rows = edges[(edges["highway"] == highway) & (edges["custom"] != "yes")]
    return set(rows["u"]) | set(rows["v"])


def custom_edge_nodes(edges):
    rows = edges[edges["custom"] == "yes"]
    return set(rows["u"]) | set(rows["v"])


# ---------------------------------------------------------------------
# Topology: how custom lines join the network
# ---------------------------------------------------------------------

def test_node_ids_are_unique_and_export_is_valid(build):
    result = build([(ROAD_ACROSS, ROAD_TAGS)])

    assert result.nodes.index.is_unique, (
        "custom node ids collide with OSM node ids: "
        f"{sorted(result.nodes.index[result.nodes.index.duplicated()])}"
    )
    assert_valid_osm_xml(result.baseline_path)
    assert_valid_osm_xml(result.custom_path)


def test_custom_road_joins_every_street_it_crosses(build):
    edges = build([(ROAD_ACROSS, ROAD_TAGS)]).edges

    osm_rows = edges[edges["custom"] != "yes"]
    osm_nodes = set(osm_rows["u"]) | set(osm_rows["v"])
    junctions = custom_edge_nodes(edges) & osm_nodes

    assert len(junctions) == N, f"expected {N} crossings, got {len(junctions)}"


def test_crossing_a_footway_splits_it_at_a_shared_node(build):
    edges = build([(ROAD_ACROSS, ROAD_TAGS)]).edges

    footway_nodes = osm_edge_nodes(edges, "footway")
    crossing = footway_nodes - FOOTWAY

    assert len(crossing) == 1, f"footway should gain one node, got {footway_nodes}"
    assert crossing <= custom_edge_nodes(edges)


def test_line_ending_near_a_node_snaps_to_it_without_a_gap(build):
    edges = build([(DIAGONAL, {"highway": "cycleway"})]).edges

    custom = edges[edges["custom"] == "yes"]
    assert [set(pair) for pair in zip(custom["u"], custom["v"], strict=True)] == [
        {node_id(2, 2), node_id(3, 3)}
    ]


def test_line_ending_on_a_node_leaves_no_second_copy_of_the_streets_there(build):
    """
    DIAGONAL ends on nodes 13 and 19, so every street there is cut at
    its own end. The zero-length offcut used to stay as an edge with the
    whole street's u and v: a copy of the footway 8-13 that skipped the
    junction ROAD_ACROSS makes on it.
    """
    edges = build([(ROAD_ACROSS, ROAD_TAGS), (DIAGONAL, {"highway": "cycleway"})]).edges

    assert not edges.duplicated(["u", "v"]).any()
    assert (edges.geometry.length > 0).all()
    footway = edges[(edges["highway"] == "footway")]
    assert not ((footway["u"] == node_id(1, 2)) & (footway["v"] == node_id(2, 2))).any()


LOOP = [(X0 + 300, Y0 + 300), (X0 + 400, Y0 + 400), (X0 + 400, Y0 + 350), (X0 + 300, Y0 + 350)]


def test_line_crossing_itself_gets_a_junction_at_the_crossing(build):
    """LOOP's last stretch runs back across its first one at (350, 350)."""
    result = build([(LOOP, ROAD_TAGS)])
    custom = result.edges[result.edges["custom"] == "yes"]

    crossing = result.nodes[result.nodes.geometry.distance(Point(X0 + 350, Y0 + 350)) < 0.01]
    assert len(crossing) == 1
    touching = (custom["u"] == crossing.index[0]) | (custom["v"] == crossing.index[0])
    assert touching.sum() == 4  # two stretches in, two out


def test_bridge_crossing_itself_has_no_junction_there(build):
    result = build([(LOOP, {**ROAD_TAGS, "bridge": "yes", "layer": "1"})])
    assert (result.nodes.geometry.distance(Point(X0 + 350, Y0 + 350)) > 1).all()


# ---------------------------------------------------------------------
# A network in several pieces (islands) is kept whole
# ---------------------------------------------------------------------

@pytest.fixture
def island(monkeypatch):
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_island())


def test_every_piece_of_the_network_is_kept(build, island):
    """OSMnx's default keeps only the largest piece; that dropped whole islands."""
    result = build([(ROAD_ACROSS, ROAD_TAGS)])

    assert set(ISLAND_NODES) <= set(result.osm_nodes.index)
    assert set(ISLAND_NODES) <= set(result.nodes.index)
    assert (result.edges["osmid"] == ISLAND_WAY).sum() == 4  # two stretches, both directions


def test_line_joined_to_a_smaller_piece_is_not_called_disconnected(build, island, caplog):
    onto_the_island = [(X0 - 80, Y0 + 200), (X0 - 60, Y0 + 250)]  # from node 402
    result = build([(onto_the_island, ROAD_TAGS)])

    assert "don't connect" not in caplog.text
    assert not disconnected_custom_edges(result.edges).any()
    assert_all_custom_edges_are_connected(result.edges)


def test_line_touching_nothing_is_still_called_disconnected(build, island, caplog):
    floating = [(X0 - 40, Y0 + 30), (X0 - 20, Y0 + 60)]
    with caplog.at_level("WARNING"):
        result = build([(ROAD_ACROSS, ROAD_TAGS), (floating, ROAD_TAGS)])

    assert "don't connect" in caplog.text
    assert disconnected_custom_edges(result.edges).sum() == 1


def test_line_can_join_two_pieces_together(build, island):
    """A new road from the island (node 401) to the grid (node 6)."""
    link = [(X0 - 80, Y0 + 100), (X0, Y0 + 100)]
    result = build([(link, ROAD_TAGS)])

    walk = result.graph("custom", "walk")
    assert route_cost(walk, ISLAND_NODES[2], node_id(4, 4), "walk") < float("inf")
    assert route_cost(result.graph("baseline", "walk"), ISLAND_NODES[2], node_id(4, 4),
                      "walk") == float("inf")


def test_street_is_not_joined_to_a_tunnel_node_just_beneath_it(build, monkeypatch):
    """
    A path starts on a tunnel's node and at once crosses the street the
    tunnel runs under, half a metre away. The path joins both - but the
    street must not be given the tunnel's node, which would let traffic
    turn between tunnel and street without using the path. (Found on a
    real tunnel in Monaco.)
    """
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_tunnel())
    path = [(X0 + 250, Y0 + 300.5), (X0 + 250, Y0 + 270)]
    edges = build([(path, {"highway": "footway"})]).edges

    street = edges[(edges["osmid"] != TUNNEL_WAY) & (edges["custom"] != "yes")]
    assert not ({*street.u, *street.v} & set(TUNNEL_NODES))

    custom = edges[edges["custom"] == "yes"]
    assert len(custom) == 2  # tunnel node -> crossing (0.5 m) -> end
    assert TUNNEL_NODES[1] in {*custom.u, *custom.v}
    crossing = ({*custom.u, *custom.v} & {*street.u, *street.v})
    assert len(crossing) == 1 and not crossing & set(TUNNEL_NODES)


def test_custom_road_is_not_joined_to_a_motorway_it_crosses(build):
    edges = build([(ROAD_TO_MOTORWAY, ROAD_TAGS)]).edges

    shared = osm_edge_nodes(edges, "motorway") & custom_edge_nodes(edges)
    assert not shared, f"at-grade junction(s) with motorway at node(s) {shared}"


def test_custom_road_ending_on_a_motorway_joins_it(build):
    """A line drawn to END on a motorway is deliberate, e.g. a new slip road."""
    slip_road = [(X0 + 400, Y0 + 100), (X0 + 500, Y0 + 200)]  # node 10 -> motorway
    edges = build([(slip_road, ROAD_TAGS)]).edges

    shared = osm_edge_nodes(edges, "motorway") & custom_edge_nodes(edges)
    assert len(shared) == 1


def test_middle_vertex_near_a_motorway_does_not_snap_onto_it(build):
    """Only a line's END points may snap onto a grade-separated way."""
    bend_near_motorway = [(X0 + 400, Y0 + 100), (X0 + 499.5, Y0 + 150), (X0 + 400, Y0 + 200)]
    edges = build([(bend_near_motorway, ROAD_TAGS)]).edges

    shared = osm_edge_nodes(edges, "motorway") & custom_edge_nodes(edges)
    assert not shared


def test_custom_bridge_crosses_streets_without_joining_them(build):
    """Diagonal from node 1 to node 25 passes over nodes 7, 13 and 19."""
    bridge = [(X0, Y0), (X0 + 400, Y0 + 400)]
    edges = build([(bridge, {**ROAD_TAGS, "bridge": "yes", "layer": "1"})]).edges

    custom = edges[edges["custom"] == "yes"]
    assert [set(pair) for pair in zip(custom["u"], custom["v"], strict=True)] == [
        {node_id(0, 0), node_id(4, 4)}
    ]


def test_custom_road_on_a_different_layer_does_not_join(build):
    edges = build([(ROAD_ACROSS, {**ROAD_TAGS, "layer": "-1"})], strict=False).edges

    osm_rows = edges[edges["custom"] != "yes"]
    junctions = custom_edge_nodes(edges) & (set(osm_rows["u"]) | set(osm_rows["v"]))
    assert not junctions


# ---------------------------------------------------------------------
# Access rules: which modes may use what
# ---------------------------------------------------------------------

def test_access_restrictions_survive_export(build):
    result = build([(ROAD_ACROSS, ROAD_TAGS)])
    bus_gate = tuple(sorted(BUS_GATE))

    for which, path in (("baseline", result.baseline_path),
                        ("custom", result.custom_path)):
        _, ways = read_osm_xml(path)
        assert any(tags.get("motor_vehicle") == "no" for _, tags in ways), (
            f"{which}: motor_vehicle=no was lost on export"
        )
        assert not result.graph(which, "drive").has_edge(*bus_gate)
        assert result.graph(which, "walk").has_edge(*bus_gate)


@pytest.mark.parametrize("features, allowed", [
    ([(ROAD_ACROSS, ROAD_TAGS)], {"drive", "bike", "walk"}),
    ([(DIAGONAL, {"highway": "cycleway"})], {"bike"}),
    ([(DIAGONAL, {"highway": "footway"})], {"walk"}),
    ([(ROAD_ACROSS, {**ROAD_TAGS, "motor_vehicle": "no"})], {"bike", "walk"}),
], ids=["road", "cycleway", "footway", "road-no-cars"])
def test_custom_edges_only_routable_by_allowed_modes(build, features, allowed):
    result = build(features)

    for mode in ROUTING_MODES:
        present = bool(custom_pairs(result.graph("custom", mode)))
        assert present == (mode in allowed), (
            f"{mode}: custom edges {'present' if present else 'missing'}"
        )


def test_per_feature_tags_override_defaults(build):
    result = build(
        [(ROAD_ACROSS, {}), (DIAGONAL, {"highway": "footway"})],
        network_tags=ROAD_TAGS,
    )

    custom = result.edges[result.edges["custom"] == "yes"]
    assert set(custom["highway"]) == {"primary", "footway"}


def test_bad_tags_fail_before_downloading(build, fake_osm):
    with pytest.raises(ValueError, match="not usable in network_type='drive'"):
        build([(DIAGONAL, {"highway": "cycleway"})], network_type="drive")

    assert fake_osm == [], "the OSM download ran despite invalid custom tags"


# ---------------------------------------------------------------------
# Routing: before vs after
# ---------------------------------------------------------------------

@pytest.mark.parametrize("mode", ROUTING_MODES)
def test_adding_a_road_never_makes_any_route_worse(build, mode):
    result = build([(ROAD_ACROSS, ROAD_TAGS)])

    before = all_pair_costs(result.graph("baseline", mode), mode)
    after = all_pair_costs(result.graph("custom", mode), mode)

    worse = {pair: (before[pair], after[pair])
             for pair in before if after[pair] > before[pair] + 1e-6}
    assert not worse, f"{mode}: {len(worse)} route(s) got worse, e.g. {list(worse.items())[:3]}"


@pytest.mark.parametrize("mode", ["drive", "walk"])
def test_cycleway_leaves_other_modes_unchanged(build, mode):
    result = build([(DIAGONAL, {"highway": "cycleway"})])

    before = all_pair_costs(result.graph("baseline", mode), mode)
    after = all_pair_costs(result.graph("custom", mode), mode)

    changed = {pair: (before[pair], after[pair])
               for pair in before if after[pair] != pytest.approx(before[pair])}
    assert not changed, f"{mode}: {len(changed)} route(s) changed, e.g. {list(changed.items())[:3]}"


def test_cycleway_shortcut_shortens_bike_route(build):
    result = build([(DIAGONAL, {"highway": "cycleway"})])
    a, b = node_id(2, 2), node_id(3, 3)

    before = route_cost(result.graph("baseline", "bike"), a, b, "bike")
    after = route_cost(result.graph("custom", "bike"), a, b, "bike")

    assert before == pytest.approx(200, abs=1)
    assert after == pytest.approx(141.4, abs=1)


# Found by Hypothesis (tests/integration/test_properties.py) and kept as
# fixed regression cases. Each once bent or rewired an existing street.
NEAR_MISS_CASES = {
    # Bend 0.95 m from node 2; the crossing of street 1-2 lands 0.94 m
    # from the bend, and street 2-3 got re-attached to it (+3.6 m).
    "bend-near-node": ([(X0, Y0), (X0 + 99.1, Y0 + 0.3), (X0 - 50, Y0 - 50)], ROAD_TAGS),
    # Ends 0.9 m from streets 1-2 and 1-6 (1.27 m from node 1): both
    # streets bent to the end point, a shortcut past node 1 for walkers.
    "end-near-corner": ([(X0 + 100, Y0), (X0 + 0.9, Y0 + 0.9)], {"highway": "cycleway"}),
    # Ends 0.9 m beside street 2-7: the street bent out to meet it.
    "end-beside-street": ([(X0, Y0), (X0 + 100.9, Y0 + 1.5)], ROAD_TAGS),
}


@pytest.mark.parametrize("case", NEAR_MISS_CASES)
@pytest.mark.parametrize("mode", ROUTING_MODES)
def test_near_miss_never_bends_existing_streets(build, case, mode):
    coords, tags = NEAR_MISS_CASES[case]
    result = build([(coords, tags)])

    before = all_pair_costs(result.graph("baseline", mode), mode)
    after = all_pair_costs(result.graph("custom", mode), mode)

    if mode in usable_modes(tags):
        bad = {p: (before[p], after[p]) for p in before if after[p] > before[p] + 1e-6}
    else:
        bad = {p: (before[p], after[p]) for p in before
               if after[p] != pytest.approx(before[p], abs=1e-6)}
    assert not bad, f"{mode}: {list(bad.items())[:3]}"


def test_line_shorter_than_snap_tolerance_is_rejected(build):
    with pytest.raises(ValueError, match="shorter than snap_tolerance"):
        build([([(X0, Y0), (X0, Y0 + 0.3)], ROAD_TAGS)])
