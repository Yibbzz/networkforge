"""
Export formats and exported content, on the synthetic grid.

XML (.osm) and PBF (.osm.pbf) exports must hold identical data, be
sorted as OSM tools expect, keep routing-relevant node and way tags,
and route identically.
"""

import osmium
import pytest

from networkforge import write_osm
from networkforge.errors import InputError
from networkforge.modes import add_travel_times, load_graph
from tests.helpers import ROUTING_MODES
from tests.integration.grid import (
    BOLLARD_NODE,
    CYCLE_LANE_TAGS,
    NO_ACCESS,
    ROAD_ACROSS,
    ROAD_TAGS,
    all_pair_costs,
)


def read_with_osmium(path):
    """(nodes, ways, type order) of an OSM file, read by pyosmium."""
    nodes, ways, order = {}, {}, []
    for obj in osmium.FileProcessor(str(path)):
        order.append((obj.type_str(), obj.id))
        if obj.is_node():
            nodes[obj.id] = (obj.location.lon, obj.location.lat, dict(obj.tags))
        elif obj.is_way():
            ways[obj.id] = ([n.ref for n in obj.nodes], dict(obj.tags))
    return nodes, ways, order


@pytest.fixture
def exported(build, tmp_path):
    """The synthetic build with ROAD_ACROSS, written in every format."""
    result = build([(ROAD_ACROSS, ROAD_TAGS)])
    paths = {suffix: tmp_path / f"network{suffix}"
             for suffix in (".osm", ".osm.pbf", ".osm.gz", ".osm.bz2")}
    for path in paths.values():
        write_osm(result.nodes, result.edges, path)
    return result, paths


@pytest.mark.parametrize("suffix", [".osm.pbf", ".osm.gz", ".osm.bz2"])
def test_every_format_holds_the_same_data_as_xml(exported, suffix):
    _, paths = exported
    xml_nodes, xml_ways, _ = read_with_osmium(paths[".osm"])
    nodes, ways, _ = read_with_osmium(paths[suffix])

    assert nodes == xml_nodes
    assert ways == xml_ways


def test_pbf_is_much_smaller_than_xml(exported):
    _, paths = exported
    assert paths[".osm.pbf"].stat().st_size < paths[".osm"].stat().st_size / 2


def test_nodes_then_ways_each_sorted_by_id(exported):
    _, paths = exported
    _, _, order = read_with_osmium(paths[".osm.pbf"])

    types = [kind for kind, _ in order]
    assert types == sorted(types, key=["n", "w"].index)
    for kind in ("n", "w"):
        ids = [obj_id for t, obj_id in order if t == kind]
        assert ids == sorted(ids)


def test_barrier_nodes_are_exported(exported):
    _, paths = exported
    nodes, _, _ = read_with_osmium(paths[".osm.pbf"])
    assert nodes[BOLLARD_NODE][2] == {"barrier": "bollard"}


def test_routing_way_tags_are_exported(exported):
    _, paths = exported
    _, ways, _ = read_with_osmium(paths[".osm.pbf"])

    lane_ways = [tags for _, tags in ways.values() if tags.get("cycleway") == "lane"]
    assert lane_ways, "cycleway=lane was dropped on export"
    for tags in lane_ways:
        assert CYCLE_LANE_TAGS.items() <= tags.items()


def test_custom_ways_are_marked(exported):
    _, paths = exported
    _, ways, _ = read_with_osmium(paths[".osm.pbf"])
    custom = [tags for _, tags in ways.values() if tags.get("nf:custom") == "yes"]
    assert custom and all(tags["highway"] == "primary" for tags in custom)


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
