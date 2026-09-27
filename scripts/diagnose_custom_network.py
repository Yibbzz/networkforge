"""
Diagnose whether the custom network in an exported OSM file is
validly integrated and actually usable by a router.

Run with: uv run python scripts/diagnose_custom_network.py

Reads the files written by scripts/get_test_data.py (no network access
needed) and prints a report covering:

  1. OSM XML validity   - every <nd ref> points at a real <node>,
                          every way has a highway tag.
  2. Provenance         - custom ways carry the 'nf:custom' tag
                          (if not, the file is stale: rerun
                          get_test_data.py).
  3. Custom tags        - highway / maxspeed (with unit check) /
                          oneway / access, which modes may use the
                          custom ways, and the speed OSMnx assigns.
  4. Connectivity       - custom edges sit in the main strongly
                          connected component; junctions with OSM
                          ways, flagging any join with a motorway /
                          bridge / tunnel part-way along a custom line.
  5. Routing            - fastest route start->end with no mode
                          filter vs per mode, and a "what speed
                          would the custom road need to win" estimate.
"""

import collections
import xml.etree.ElementTree as ET

import geopandas as gpd
import networkx as nx
import osmnx as ox

from networkforge.modes import (
    add_travel_times,
    filter_graph_by_mode,
    keep_mode_tags,
    usable_modes,
)
from networkforge.topology import is_grade_separated

OSM_FILE = "tests/data/custom_network.osm"
START_FILE = "tests/data/start_point.geojson"
END_FILE = "tests/data/end_point.geojson"

def header(title):
    print(f"\n=== {title} " + "=" * max(0, 56 - len(title)))


def ok(msg):
    print(f"  [OK]   {msg}")


def warn(msg):
    print(f"  [WARN] {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")


def check_xml(path):
    header("1. OSM XML validity")

    node_ids = set()
    custom_ways = []
    dangling = 0
    no_highway = 0
    n_ways = 0

    for _, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "node":
            node_ids.add(elem.get("id"))
        elif elem.tag == "way":
            n_ways += 1
            refs = [nd.get("ref") for nd in elem.findall("nd")]
            tags = {t.get("k"): t.get("v") for t in elem.findall("tag")}
            dangling += sum(r not in node_ids for r in refs)
            if "highway" not in tags:
                no_highway += 1
            if tags.get("nf:custom") == "yes":
                custom_ways.append(tags)
            elem.clear()

    print(f"  {len(node_ids):,} nodes, {n_ways:,} ways")
    (fail if dangling else ok)(f"{dangling} way node refs with no matching <node>")
    (warn if no_highway else ok)(f"{no_highway} ways without a highway tag")

    header("2. Provenance (nf:custom)")
    if not custom_ways:
        fail(
            "no ways tagged nf:custom=yes. Either the file predates the "
            "export.py change (rerun scripts/get_test_data.py) or no custom "
            "edges survived the build. Every check below that needs custom "
            "edges is skipped."
        )
    else:
        ok(f"{len(custom_ways)} custom ways")

    return custom_ways


def check_tags(custom_ways, graph, custom_edges):
    header("3. Custom tags")

    for key in ["highway", "maxspeed", "oneway", "access", "lanes"]:
        values = collections.Counter(w.get(key, "<missing>") for w in custom_ways)
        print(f"  {key:9s} {dict(values)}")

    modes = collections.Counter(", ".join(usable_modes(w)) or "NOTHING" for w in custom_ways)
    print(f"  usable by {dict(modes)}")

    maxspeeds = {w.get("maxspeed") for w in custom_ways if w.get("maxspeed")}
    unitless = [m for m in maxspeeds if m.replace(".", "").isdigit()]
    if unitless:
        warn(
            f"maxspeed {unitless} has no unit, so OSM/OSMnx read it as km/h. "
            "If you meant mph (UK), use e.g. '50 mph'."
        )

    speeds = collections.Counter(
        round(graph.edges[e]["speed_kph"], 1) for e in custom_edges
    )
    print(f"  speed_kph assigned by OSMnx: {dict(speeds)}")


def check_connectivity(graph, custom_edges):
    header("4. Connectivity")

    largest = max(nx.strongly_connected_components(graph), key=len)
    stranded = [e for e in custom_edges if e[0] not in largest or e[1] not in largest]
    (fail if stranded else ok)(
        f"{len(stranded)} of {len(custom_edges)} custom edges outside the main "
        "strongly connected component"
    )

    custom_nodes = {n for e in custom_edges for n in e[:2]}
    custom_pairs = {e[:2] for e in custom_edges}
    junctions = [
        n for n in custom_nodes
        if any((n, nb) not in custom_pairs for nb in graph.successors(n))
    ]
    print(f"  {len(junctions)} of {len(custom_nodes)} custom nodes are junctions with OSM ways")

    # A custom node with one custom neighbour is the end of a custom line.
    custom_neighbours = collections.defaultdict(set)
    for u, v in custom_pairs:
        custom_neighbours[u].add(v)
        custom_neighbours[v].add(u)

    junction_types = collections.Counter()
    separated_at_end, separated_mid_line = set(), set()
    for n in junctions:
        osm_ways = [
            d for nb in graph.successors(n) if (n, nb) not in custom_pairs
            for d in graph[n][nb].values()
        ]
        junction_types.update(str(d.get("highway")) for d in osm_ways)
        # Only a problem if EVERY OSM way here is grade-separated: a node
        # where e.g. an ordinary street meets a tunnel portal is a real,
        # at-grade junction with the street.
        if all(is_grade_separated(d) for d in osm_ways):
            if len(custom_neighbours[n]) == 1:
                separated_at_end.add(n)
            else:
                separated_mid_line.add(n)
    print(f"  joined to highway types: {dict(junction_types.most_common(8))}")
    print(f"  {len(separated_at_end)} custom line end(s) join a motorway/bridge/tunnel "
          "(deliberate, e.g. a slip road)")
    (fail if separated_mid_line else ok)(
        f"{len(separated_mid_line)} junctions with motorways/bridges/tunnels part-way "
        "along a custom line (crossings should pass over/under)"
    )


def route(graph, start, end, custom_edges, label, weight="travel_time"):
    a = ox.distance.nearest_nodes(graph, start.x, start.y)
    b = ox.distance.nearest_nodes(graph, end.x, end.y)
    path = nx.shortest_path(graph, a, b, weight=weight)
    pairs = list(zip(path, path[1:], strict=False))
    custom_pairs = {e[:2] for e in custom_edges}
    used = sum(p in custom_pairs for p in pairs)
    minutes = nx.path_weight(graph, path, "travel_time") / 60
    metres = nx.path_weight(graph, path, "length")
    mix = collections.Counter(
        str(min(graph[u][v].values(), key=lambda d: d[weight]).get("highway"))
        for u, v in pairs
    )
    print(f"  {label:22s} {minutes:5.1f} min {metres:7.0f} m  "
          f"custom edges used: {used}/{len(pairs)}")
    print(f"  {'':22s} mix: {dict(mix.most_common(5))}")
    return minutes


def check_routing(graph, custom_edges):
    header("5. Routing")

    start = gpd.read_file(START_FILE).to_crs("EPSG:4326").geometry.iloc[0]
    end = gpd.read_file(END_FILE).to_crs("EPSG:4326").geometry.iloc[0]

    all_minutes = route(graph, start, end, custom_edges, "no mode filter")

    route(filter_graph_by_mode(graph, "drive"), start, end, custom_edges, "drive")
    # OSMnx speeds are car speeds, so bikes route by distance instead.
    route(filter_graph_by_mode(graph, "bike"), start, end, custom_edges,
          "bike (shortest)", weight="length")

    # Fastest route forced onto custom edges as much as possible:
    # the shortest-by-length path, which a direct custom road should win.
    a = ox.distance.nearest_nodes(graph, start.x, start.y)
    b = ox.distance.nearest_nodes(graph, end.x, end.y)
    path = nx.shortest_path(graph, a, b, weight="length")
    custom_pairs = {e[:2] for e in custom_edges}
    custom_len = sum(
        min(d["length"] for d in graph[u][v].values())
        for u, v in zip(path, path[1:], strict=False) if (u, v) in custom_pairs
    )
    if custom_len:
        needed = custom_len / 1000 / (all_minutes / 60)
        print(
            f"\n  Shortest-by-length route uses {custom_len:.0f} m of custom road. "
            f"To beat {all_minutes:.1f} min it needs to average > {needed:.0f} km/h "
            f"(~{needed / 1.609:.0f} mph)."
        )


def main():
    custom_ways = check_xml(OSM_FILE)
    if not custom_ways:
        return

    keep_mode_tags()
    graph = ox.graph_from_xml(OSM_FILE, simplify=False)
    graph = add_travel_times(graph)
    custom_edges = [e for e, d in graph.edges.items() if d.get("nf:custom") == "yes"]

    check_tags(custom_ways, graph, custom_edges)
    check_connectivity(graph, custom_edges)
    check_routing(graph, custom_edges)


if __name__ == "__main__":
    main()
