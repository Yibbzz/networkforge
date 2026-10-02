"""
Export formats and exported content, on the synthetic grid.

XML (.osm) and PBF (.osm.pbf) exports must hold identical data, be
sorted as OSM tools expect, keep routing-relevant node and way tags,
and route identically.
"""

import geopandas as gpd
import osmium
import osmnx as ox
import pytest
from shapely.geometry import LineString, box

from networkforge import osm, write_osm
from networkforge.errors import InputError
from networkforge.modes import add_travel_times, load_graph
from tests.helpers import ROUTING_MODES, build_and_export
from tests.integration.grid import (
    BBOX,
    BOLLARD_NODE,
    CYCLE_LANE_TAGS,
    FERRY_ID,
    FERRY_NODE,
    NO_ACCESS,
    RESTRICTION_ID,
    ROAD_ACROSS,
    ROAD_TAGS,
    UTM,
    X0,
    Y0,
    N,
    all_pair_costs,
    grid_elements,
    grid_with_ferry,
    synthetic_graph,
)

RESTRICTION = next(e for e in grid_elements() if e["type"] == "relation")


def read_with_osmium(path):
    """(nodes, ways, relations, type order) of an OSM file, read by pyosmium."""
    nodes, ways, relations, order = {}, {}, {}, []
    for obj in osmium.FileProcessor(str(path)):
        order.append((obj.type_str(), obj.id))
        if obj.is_node():
            nodes[obj.id] = (obj.location.lon, obj.location.lat, dict(obj.tags))
        elif obj.is_way():
            ways[obj.id] = ([n.ref for n in obj.nodes], dict(obj.tags))
        else:
            relations[obj.id] = ([(m.type, m.ref, m.role) for m in obj.members], dict(obj.tags))
    return nodes, ways, relations, order


@pytest.fixture
def exported(build, tmp_path):
    """The synthetic build with ROAD_ACROSS, written in every format."""
    result = build([(ROAD_ACROSS, {**ROAD_TAGS, "name": "New Road"})])
    paths = {suffix: tmp_path / f"network{suffix}"
             for suffix in (".osm", ".osm.pbf", ".osm.gz", ".osm.bz2")}
    for path in paths.values():
        write_osm(result.nodes, result.edges, path)
    write_osm(result.osm_nodes, result.osm_edges, tmp_path / "before.osm.pbf")
    return result, paths


@pytest.fixture
def source_ways():
    return {e["id"]: (e["nodes"], e["tags"]) for e in grid_elements() if e["type"] == "way"}


@pytest.mark.parametrize("suffix", [".osm.pbf", ".osm.gz", ".osm.bz2"])
def test_every_format_holds_the_same_data_as_xml(exported, suffix):
    _, paths = exported
    xml_nodes, xml_ways, xml_relations, _ = read_with_osmium(paths[".osm"])
    nodes, ways, relations, _ = read_with_osmium(paths[suffix])

    assert nodes == xml_nodes
    assert ways == xml_ways
    assert relations == xml_relations and relations


def test_pbf_is_much_smaller_than_xml(exported):
    _, paths = exported
    assert paths[".osm.pbf"].stat().st_size < paths[".osm"].stat().st_size / 2


def test_nodes_ways_relations_each_sorted_by_id(exported):
    _, paths = exported
    *_, order = read_with_osmium(paths[".osm.pbf"])

    types = [kind for kind, _ in order]
    assert types == sorted(types, key=["n", "w", "r"].index)
    for kind in ("n", "w", "r"):
        ids = [obj_id for t, obj_id in order if t == kind]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------
# The existing network is written as OpenStreetMap has it
# ---------------------------------------------------------------------

def test_before_network_is_the_osm_data_unchanged(exported, source_ways):
    """Same way ids, node order and tags: nothing renumbered, split or dropped."""
    _, paths = exported
    _, ways, relations, _ = read_with_osmium(paths[".osm"].parent / "before.osm.pbf")

    assert ways == source_ways
    assert relations == {RESTRICTION_ID: (
        [(kind[0], ref, role) for kind, ref, role in
         ((m["type"], m["ref"], m["role"]) for m in RESTRICTION["members"])],
        RESTRICTION["tags"],
    )}


def test_two_way_street_is_written_once(exported):
    """OSMnx holds each two-way street as two edges; that must not reach the file."""
    _, paths = exported
    _, ways, _, _ = read_with_osmium(paths[".osm.pbf"])
    segments = [frozenset(pair) for refs, _ in ways.values()
                for pair in zip(refs, refs[1:], strict=False)]
    assert len(segments) == len(set(segments))


def test_crossed_ways_keep_their_id_and_gain_only_the_junction_node(exported, source_ways):
    result, paths = exported
    _, ways, _, _ = read_with_osmium(paths[".osm.pbf"])
    new_nodes = set(result.nodes.index) - set(result.osm_nodes.index)

    changed = 0
    for way_id, (refs, tags) in source_ways.items():
        after_refs, after_tags = ways[way_id]
        assert after_tags == tags
        assert [ref for ref in after_refs if ref not in new_nodes] == refs
        changed += after_refs != refs
    assert changed == N  # ROAD_ACROSS crosses the five columns


def test_custom_line_is_one_way_through_its_junctions(exported, source_ways):
    result, paths = exported
    _, ways, _, _ = read_with_osmium(paths[".osm.pbf"])

    (way_id,) = set(ways) - set(source_ways)
    refs, tags = ways[way_id]
    assert way_id > max(source_ways)
    assert tags == {**ROAD_TAGS, "name": "New Road", "nf:custom": "yes"}
    assert len(refs) == N + 2  # two ends and the five crossings

    x = result.nodes.geometry.x.loc[refs].to_list()
    assert x == sorted(x), "nodes are not in order along the line"
    for ref in refs[1:-1]:
        assert sum(ref in other for other, _ in ways.values()) == 2  # the junctions


def test_turn_restriction_survives_the_build(exported):
    _, paths = exported
    _, ways, relations, _ = read_with_osmium(paths[".osm.pbf"])

    members, tags = relations[RESTRICTION_ID]
    assert tags == {"type": "restriction", "restriction": "no_left_turn"}
    (_, from_way, _), (_, via, _), (_, to_way, _) = members
    assert via in ways[from_way][0] and via in ways[to_way][0]


def test_osm_tags_without_a_column_reach_the_file(build, tmp_path, monkeypatch):
    """Tags the edge table has no column for are still written (routers read many)."""
    elements = grid_elements()
    elements[N * N + 2]["tags"]["maxweightrating:hgv"] = "7.5"
    elements[0]["tags"] = {"highway": "traffic_signals", "traffic_signals:direction": "forward"}
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, network_type: elements)

    result = build([(ROAD_ACROSS, ROAD_TAGS)])
    write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
    nodes, ways, _, _ = read_with_osmium(tmp_path / "after.osm.pbf")

    assert "maxweightrating:hgv" not in result.edges.columns
    assert ways[elements[N * N + 2]["id"]][1]["maxweightrating:hgv"] == "7.5"
    assert nodes[elements[0]["id"]][2]["traffic_signals:direction"] == "forward"


def test_cropped_way_is_written_as_the_part_inside(tmp_path, fake_osm):
    """A box around the bottom-left blocks cuts the long streets short."""
    corner = gpd.GeoDataFrame(geometry=[box(X0 - 10, Y0 - 10, X0 + 210, Y0 + 210)], crs=UTM)
    street = LineString([(X0 + 50, Y0), (X0 + 50, Y0 + 200)])
    line = gpd.GeoDataFrame([ROAD_TAGS], geometry=[street], crs=UTM)
    result = build_and_export(corner, line, tmp_path)
    write_osm(result.osm_nodes, result.osm_edges, tmp_path / "before.osm.pbf")
    nodes, ways, relations, _ = read_with_osmium(tmp_path / "before.osm.pbf")

    assert ways[1][0] == [1, 2, 3]  # Row 0 Street, five nodes long in OSM
    assert all(ref in nodes for refs, _ in ways.values() for ref in refs)
    assert relations == {}  # the restriction's ways are outside the box


# ---------------------------------------------------------------------
# Ferries: not streets, but written for routers
# ---------------------------------------------------------------------

def write_elements(elements, path):
    writer = osmium.SimpleWriter(str(path))
    for element in elements:
        if element["type"] == "node":
            writer.add_node(osmium.osm.mutable.Node(
                id=element["id"], location=(element["lon"], element["lat"]),
                tags=element["tags"]))
        elif element["type"] == "way":
            writer.add_way(osmium.osm.mutable.Way(
                id=element["id"], nodes=element["nodes"], tags=element["tags"]))
    writer.close()


@pytest.fixture(params=["download", "extract"])
def ferry_build(request, tmp_path, monkeypatch):
    """A build on the grid with a ferry, from Overpass (faked) or a local extract."""
    custom = gpd.GeoDataFrame([ROAD_TAGS], geometry=[LineString(ROAD_ACROSS)], crs=UTM)
    if request.param == "extract":
        write_elements(grid_with_ferry(), tmp_path / "grid.osm.pbf")
        result = build_and_export(BBOX, custom, tmp_path, osm_source=tmp_path / "grid.osm.pbf")
    else:
        monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_ferry())
        result = build_and_export(BBOX, custom, tmp_path)
    write_osm(result.osm_nodes, result.osm_edges, tmp_path / "before.osm.pbf")
    write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
    return result, tmp_path


def test_ferry_is_written_to_both_files_as_it_is(ferry_build):
    result, folder = ferry_build
    ferry = next(e for e in grid_with_ferry() if e["id"] == FERRY_ID)

    for name in ("before.osm.pbf", "after.osm.pbf"):
        nodes, ways, _, order = read_with_osmium(folder / name)
        assert ways[FERRY_ID] == (ferry["nodes"], ferry["tags"])
        assert FERRY_NODE in nodes
        ids = [obj_id for kind, obj_id in order if kind == "n"]
        assert ids == sorted(ids)


def test_ferry_is_not_part_of_the_street_network(ferry_build):
    result, _ = ferry_build
    assert FERRY_NODE not in result.nodes.index
    assert FERRY_ID not in set(result.edges["osmid"].dropna())
    assert len(result.osm_edges) == len(ox.graph_to_gdfs(synthetic_graph(), nodes=False))


def test_ferry_is_cropped_to_the_area(tmp_path, monkeypatch):
    """A box around the bottom-left block: the ferry's far end (node 13) is outside."""
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_ferry())
    corner = gpd.GeoDataFrame(geometry=[box(X0 - 10, Y0 - 10, X0 + 150, Y0 + 150)], crs=UTM)
    street = LineString([(X0 + 50, Y0), (X0 + 50, Y0 + 100)])
    line = gpd.GeoDataFrame([ROAD_TAGS], geometry=[street], crs=UTM)
    result = build_and_export(corner, line, tmp_path)
    write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
    nodes, ways, _, _ = read_with_osmium(tmp_path / "after.osm.pbf")

    assert ways[FERRY_ID][0] == [1, FERRY_NODE]
    assert all(ref in nodes for refs, _ in ways.values() for ref in refs)


def test_tables_without_a_source_are_still_written(tmp_path):
    """Straight from OSMnx (no build): ways are rebuilt from osmid and the columns."""
    nodes, edges = ox.graph_to_gdfs(synthetic_graph())
    write_osm(nodes, edges.reset_index(), tmp_path / "grid.osm.pbf")
    _, ways, relations, _ = read_with_osmium(tmp_path / "grid.osm.pbf")

    assert ways[1] == ([1, 2, 3, 4, 5], {"highway": "residential", "name": "Row 0 Street",
                                         "maxspeed": "30 mph", "oneway": "no"})
    assert ways[19][1]["oneway"] == "yes"
    assert relations == {}


def test_barrier_nodes_are_exported(exported):
    _, paths = exported
    nodes, *_ = read_with_osmium(paths[".osm.pbf"])
    assert nodes[BOLLARD_NODE][2] == {"barrier": "bollard"}


def test_routing_way_tags_are_exported(exported):
    _, paths = exported
    _, ways, _, _ = read_with_osmium(paths[".osm.pbf"])

    lane_ways = [tags for _, tags in ways.values() if tags.get("cycleway") == "lane"]
    assert lane_ways, "cycleway=lane was dropped on export"
    for tags in lane_ways:
        assert CYCLE_LANE_TAGS.items() <= tags.items()


def test_custom_ways_are_marked(exported):
    _, paths = exported
    _, ways, _, _ = read_with_osmium(paths[".osm.pbf"])
    custom = [tags for _, tags in ways.values() if tags.get("nf:custom") == "yes"]
    assert custom and all(tags["highway"] == "primary" for tags in custom)


def test_oneway_is_written_as_osm_yes_no(exported):
    """OSMnx stores oneway as True/False; OSM needs yes/no."""
    _, paths = exported
    _, ways, _, _ = read_with_osmium(paths[".osm.pbf"])
    values = {tags["oneway"] for _, tags in ways.values() if "oneway" in tags}
    assert values == {"yes", "no"}


def test_one_way_motorway_stays_one_way_after_reload(build):
    # A slip road joins the (otherwise unconnected) motorway to the grid,
    # so OSMnx keeps it when loading.
    slip_road = [(X0 + 400, Y0 + 100), (X0 + 500, Y0 + 200)]
    drive = build([(slip_road, ROAD_TAGS)]).graph("custom", "drive")

    motorway = [(u, v) for u, v, d in drive.edges(data=True) if d.get("highway") == "motorway"]
    assert motorway, "motorway missing from the drive graph"
    for u, v in motorway:
        assert not drive.has_edge(v, u), f"motorway {u}->{v} became two-way"


def test_access_no_street_is_closed_to_cars_open_to_walkers(exported):
    result, _ = exported
    street = tuple(sorted(NO_ACCESS))

    assert not result.graph("custom", "drive").has_edge(*street)
    assert not result.graph("custom", "bike").has_edge(*street)
    assert result.graph("custom", "walk").has_edge(*street)


@pytest.mark.parametrize("mode", ROUTING_MODES)
def test_pbf_routes_exactly_like_xml(exported, tmp_path, mode):
    """OSMnx only reads XML, so convert the PBF back with pyosmium first."""
    result, paths = exported
    round_trip = tmp_path / "from_pbf.osm"
    writer = osmium.SimpleWriter(str(round_trip))
    for obj in osmium.FileProcessor(str(paths[".osm.pbf"])):
        writer.add(obj)
    writer.close()

    from_xml = all_pair_costs(result.graph("custom", mode), mode)
    graph = load_graph(str(round_trip), mode)
    if mode == "drive":
        graph = add_travel_times(graph)

    assert all_pair_costs(graph, mode) == pytest.approx(from_xml)


def test_existing_file_is_replaced(exported):
    result, paths = exported
    write_osm(result.nodes, result.edges, paths[".osm.pbf"])  # no error


def test_unknown_format_rejected(exported, tmp_path):
    result, _ = exported
    with pytest.raises(InputError, match="Use one of: .osm, .osm.pbf"):
        write_osm(result.nodes, result.edges, tmp_path / "network.shp")
