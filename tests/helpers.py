"""
Shared helpers for the integration tests (tests/integration/test_synthetic_grid.py
and tests/live/test_cities.py): build + export a baseline and custom
network, load them per mode, and compare routes.
"""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import networkx as nx

from networkforge.export import write_osm_xml
from networkforge.modes import add_travel_times, load_graph
from networkforge.network import build_network

ROUTING_MODES = ("drive", "bike", "walk")

# OSMnx speeds are car speeds, so bike and walk costs are distance
# (time at a constant speed is proportional to it).
WEIGHT = {"drive": "travel_time", "bike": "length", "walk": "length"}


@dataclass
class Build:
    nodes: gpd.GeoDataFrame
    edges: gpd.GeoDataFrame
    osm_nodes: gpd.GeoDataFrame
    osm_edges: gpd.GeoDataFrame
    baseline_path: Path
    custom_path: Path
    _graphs: dict = field(default_factory=dict, repr=False)

    def graph(self, which: str, mode: str) -> nx.MultiDiGraph:
        """Routable graph for `mode`, from the "baseline" or "custom" export."""
        key = (which, mode)
        if key not in self._graphs:
            path = self.baseline_path if which == "baseline" else self.custom_path
            graph = load_graph(str(path), mode)
            if mode == "drive":
                graph = add_travel_times(graph)
            self._graphs[key] = graph
        return self._graphs[key]


def build_and_export(
    bbox_gdf: gpd.GeoDataFrame,
    custom_gdf: gpd.GeoDataFrame,
    out_dir: Path,
    network_tags: dict | None = None,
    **build_kwargs,
) -> Build:
    """Run build_network, then export baseline and custom OSM XML."""
    nodes, edges, osm_nodes, osm_edges = build_network(
        bbox_gdf,
        custom_gdf,
        network_tags or {},
        return_source_osm=True,
        **build_kwargs,
    )

    baseline_path = Path(out_dir) / "baseline.osm"
    custom_path = Path(out_dir) / "custom.osm"
    write_osm_xml(osm_nodes, osm_edges, str(baseline_path))
    write_osm_xml(nodes, edges, str(custom_path))

    return Build(nodes, edges, osm_nodes, osm_edges, baseline_path, custom_path)


def route_cost(graph: nx.MultiDiGraph, origin, destination, mode: str) -> float:
    """Cheapest route cost for `mode`, or inf if there is no route."""
    try:
        return nx.shortest_path_length(graph, origin, destination, weight=WEIGHT[mode])
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return math.inf


def custom_pairs(graph: nx.MultiDiGraph) -> set:
    """(u, v) of every edge carrying nf:custom=yes."""
    return {
        (u, v) for u, v, data in graph.edges(data=True)
        if data.get("nf:custom") == "yes"
    }


def read_osm_xml(path: Path) -> tuple[list[str], list[tuple[list[str], dict]]]:
    """Node ids and (node refs, tags) of every way in an OSM XML file."""
    node_ids, ways = [], []
    for element in ET.parse(path).getroot():
        if element.tag == "node":
            node_ids.append(element.get("id"))
        elif element.tag == "way":
            refs = [nd.get("ref") for nd in element.findall("nd")]
            tags = {tag.get("k"): tag.get("v") for tag in element.findall("tag")}
            ways.append((refs, tags))
    return node_ids, ways


def assert_valid_osm_xml(path: Path) -> None:
    """Unique node ids, and every way node ref points at a real node."""
    node_ids, ways = read_osm_xml(path)

    duplicates = len(node_ids) - len(set(node_ids))
    assert duplicates == 0, f"{path.name}: {duplicates} duplicate node id(s)"

    known = set(node_ids)
    dangling = [ref for refs, _ in ways for ref in refs if ref not in known]
    assert not dangling, f"{path.name}: way refs with no <node>: {dangling[:10]}"


# Columns QGIS's network tools are pointed at, per routing mode.
MODE_COLUMN = {"drive": "car", "bike": "bike", "walk": "walk"}


def qgis_graph(edges: gpd.GeoDataFrame, mode: str) -> nx.DiGraph:
    """
    Directed graph from GeoPackage analysis edges, read the way QGIS's
    network tools read the layer: filtered to the mode's column, with the
    mode's direction field (walking: both ways), weighted by length_m.
    """
    column = MODE_COLUMN[mode]
    graph = nx.DiGraph()
    for row in edges[edges[column]].itertuples():
        way = "both" if mode == "walk" else getattr(row, f"{column}_direction")
        u, v = int(row.u), int(row.v)
        if way in ("forward", "both"):
            graph.add_edge(u, v, length=row.length_m, minutes=row.car_minutes)
        if way in ("backward", "both"):
            graph.add_edge(v, u, length=row.length_m, minutes=row.car_minutes)
    return graph
