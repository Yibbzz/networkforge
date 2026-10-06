"""
Getting the existing OSM network: from the Overpass API (small areas)
or from a local OSM file such as a Geofabrik .osm.pbf extract (any size).

Both return the same thing: nodes and edges GeoDataFrames in the
analysis CRS, built as ox.graph_from_bbox builds them with
retain_all=True (500 m buffer, cropped to the box, every piece of
network kept), so a build from a local extract matches a build from
Overpass for the same area and data.

Both also keep the OSM data itself (OSMSource: every way with its
nodes and all its tags, tagged nodes, turn restrictions and route
relations), attached to the edges as `edges.attrs[SOURCE_ATTR]`. The
edge table only holds the tags OSMnx is told to keep; export writes the
existing network from the source, so routers get OSM as it is.
"""

import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmium
import osmnx as ox
import requests
import shapely
from osmnx import _overpass
from osmnx._errors import InsufficientResponseError, ResponseStatusCodeError
from osmnx.graph import _create_graph
from pyproj import CRS
from shapely.geometry import box

from .errors import InputError, OSMDownloadError
from .modes import _passes_osmnx_filter, is_ferry, keep_mode_tags
from .tags import FERRY_ROUTES, RELATION_TYPES, ROUTE_RELATIONS, ROUTING_NODE_KEYS

log = logging.getLogger(__name__)

# Where the OSMSource travels: edges.attrs[SOURCE_ATTR].
SOURCE_ATTR = "networkforge_osm"

# OSMnx's built-in network filters, applied in the Overpass query.
# "all" (the default) keeps every mode's ways plus their access tags,
# so one build can be routed for any mode later (see modes.py).
# Narrower types drop ways at download, and custom lines can then
# only be joined to what's left.
NETWORK_TYPES = ("all", "all_public", "bike", "drive", "drive_service", "walk")

# ox.graph_from_bbox downloads this far around the box, then crops.
BUFFER_M = 500


def configure_osmnx_cache() -> None:
    """
    Configure the OSMnx cache directory from the
    NETWORKFORGE_OSMNX_CACHE environment variable.
    """
    cache_folder = os.getenv(
        "NETWORKFORGE_OSMNX_CACHE",
        os.path.expanduser("~/.cache/networkforge/osmnx"),
    )

    os.makedirs(cache_folder, exist_ok=True)

    ox.settings.cache_folder = cache_folder


def _check_network_type(network_type: str) -> None:
    if network_type not in NETWORK_TYPES:
        raise InputError(
            f"Unknown network_type {network_type!r}. "
            f"Choose one of: {', '.join(NETWORK_TYPES)}"
        )


@dataclass(frozen=True, eq=False)
class OSMSource:
    """
    The area's OpenStreetMap data as OSM has it. Elements use the
    Overpass JSON shapes OSMnx reads.

    ways:       way id -> (node ids in order, all tags)
    node_tags:  node id -> all tags, for nodes a router acts on
                (barriers, signals, crossings ... see ROUTING_NODE_KEYS)
    relations:  (id, tags, [(member type "n"/"w"/"r", ref, role), ...])
                for turn restrictions and routes that use a kept way
    ferries:    way id -> (node ids, all tags) of ferry routes inside the
                area (see FERRY_ROUTES): written to OSM files as they are
    ferry_nodes: node id -> (lon, lat) of the ferries' nodes
    """

    ways: dict[int, tuple[list[int], dict[str, str]]] = field(default_factory=dict)
    node_tags: dict[int, dict[str, str]] = field(default_factory=dict)
    relations: list[tuple[int, dict[str, str], list[tuple[str, int, str]]]] = field(
        default_factory=list)
    ferries: dict[int, tuple[list[int], dict[str, str]]] = field(default_factory=dict)
    ferry_nodes: dict[int, tuple[float, float]] = field(default_factory=dict)

    # pandas deep-copies .attrs on most operations; the source is
    # read-only, so share it rather than copy a whole city each time.
    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self


def _keeps_relation(tags: dict[str, str]) -> bool:
    kind = tags.get("type", "")
    if kind in RELATION_TYPES or kind.startswith("restriction:"):
        return True
    return kind == "route" and tags.get("route") in ROUTE_RELATIONS


def _source_from_elements(elements: list[dict], polygon) -> OSMSource:
    """`polygon`: the area itself (not buffered); ferries are cropped to it."""
    ways, node_tags, relations = {}, {}, []
    ferries, ferry_nodes, locations = {}, {}, {}
    for element in elements:
        tags = element.get("tags") or {}
        if element["type"] == "way" and not is_ferry(tags):
            ways[element["id"]] = (list(element["nodes"]), dict(tags))
        elif element["type"] == "node":
            locations[element["id"]] = (element["lon"], element["lat"])
            if any(key in tags for key in ROUTING_NODE_KEYS):
                node_tags[element["id"]] = dict(tags)

    extra_ids = iter(range(-(10**15), 0))  # for further pieces of a cropped ferry
    for element in elements:
        if element["type"] != "way" or not is_ferry(element.get("tags") or {}):
            continue
        inside = [(ref, locations[ref]) if ref in locations and shapely.contains_xy(
            polygon, *locations[ref]) else (ref, None) for ref in element["nodes"]]
        for number, run in enumerate(_known_runs(inside)):
            way_id = element["id"] if number == 0 else next(extra_ids)
            ferries[way_id] = ([ref for ref, _ in run], dict(element["tags"]))
            ferry_nodes.update(run)
    for element in elements:
        if element["type"] != "relation" or not _keeps_relation(element.get("tags") or {}):
            continue
        members = [(m["type"][0], m["ref"], m.get("role", "")) for m in element["members"]]
        if any(kind == "w" and ref in ways for kind, ref, _ in members):
            relations.append((element["id"], dict(element["tags"]), members))
    return OSMSource(ways, node_tags, relations, ferries, ferry_nodes)


def _network_from_elements(
    elements: list[dict],
    polygon,
    polygon_buffered,
    network_type: str,
    analysis_crs: CRS,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Nodes and edges from OSM elements covering `polygon_buffered`: the
    same steps as ox.graph_from_polygon (what graph_from_bbox uses).
    """
    # The street network: every way but the ferries, and those ways' nodes.
    streets = [e for e in elements
               if e["type"] == "way" and not is_ferry(e.get("tags") or {})]
    on_streets = {ref for way in streets for ref in way["nodes"]}
    graph_buffered = _create_graph(
        [{"elements": streets + [e for e in elements
                                 if e["type"] == "node" and e["id"] in on_streets]}],
        bidirectional=network_type in ox.settings.bidirectional_network_types,
    )
    # Every piece of network in the area is kept (OSMnx's retain_all), not
    # only the largest: other islands, streets that join the rest outside
    # the area, isolated paths. They are part of OpenStreetMap, routers
    # use them, and Valhalla sets speeds by how dense the streets around
    # an edge are - leaving pieces out changed travel times on the rest.
    graph_buffered = ox.truncate.truncate_graph_polygon(graph_buffered, polygon_buffered)
    graph = ox.truncate.truncate_graph_polygon(graph_buffered, polygon)
    street_counts = ox.stats.count_streets_per_node(graph_buffered, nodes=graph.nodes)
    nx.set_node_attributes(graph, values=street_counts, name="street_count")

    nodes, edges = ox.graph_to_gdfs(graph)
    nodes, edges = nodes.to_crs(analysis_crs), edges.to_crs(analysis_crs).reset_index()
    edges.attrs[SOURCE_ATTR] = _source_from_elements(elements, polygon)
    return nodes, edges


def _buffered(polygon, bbox: gpd.GeoDataFrame):
    """`polygon` (WGS84) grown by BUFFER_M, as ox.graph_from_polygon does."""
    utm = bbox.estimate_utm_crs()
    buffered = gpd.GeoSeries([polygon], crs="EPSG:4326").to_crs(utm).buffer(BUFFER_M)
    return buffered.to_crs("EPSG:4326").iloc[0]


def get_osm_data_from_bbox(
    bbox: gpd.GeoDataFrame,
    analysis_crs: CRS,
    network_type: str = "all",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Download an OSM network within a bounding box from the Overpass API."""

    _check_network_type(network_type)

    # Keep motor_vehicle / foot / bicycle etc., which OSMnx drops by
    # default, so access restrictions survive into the edge table.
    keep_mode_tags()

    # Otherwise OSMnx caches responses in ./cache wherever it's run from.
    configure_osmnx_cache()

    polygon = box(*bbox.to_crs("EPSG:4326").total_bounds)
    polygon_buffered = _buffered(polygon, bbox)

    try:
        elements = _download_with_retries(polygon_buffered, network_type)
        return _network_from_elements(
            elements, polygon, polygon_buffered, network_type, analysis_crs)
    except (InsufficientResponseError, ValueError) as exc:
        raise OSMDownloadError(
            f"OpenStreetMap has no {network_type!r} ways in this bounding box "
            f"({exc}). Check the box covers an area with streets."
        ) from exc
    except (requests.RequestException, ResponseStatusCodeError) as exc:
        raise OSMDownloadError(
            f"Couldn't download OpenStreetMap data from the Overpass API ({exc}). "
            "Check your internet connection, or try again later - Overpass "
            "rate-limits heavy use."
        ) from exc


# Seconds to wait before each new attempt when Overpass can't be reached
# (it refuses connections for a while when busy).
DOWNLOAD_RETRY_WAITS = (10, 30)


def _download_with_retries(polygon, network_type: str) -> list[dict]:
    """_download_elements, tried again after a pause if Overpass can't be reached."""
    for wait in (*DOWNLOAD_RETRY_WAITS, None):
        try:
            return _download_elements(polygon, network_type)
        except requests.ConnectionError as exc:
            if wait is None:
                raise
            log.warning("Couldn't reach the Overpass API (%s); trying again in %d s.",
                        type(exc).__name__, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def _download_elements(polygon, network_type: str) -> list[dict]:
    """
    Every way OSMnx's filter for `network_type` matches inside `polygon`,
    with its nodes (OSMnx's own query), plus the turn restrictions and
    routes those ways belong to, and the ferry routes in the area with
    their nodes (a second query). Overpass JSON elements.
    """
    elements = [
        element
        for response in _overpass._download_overpass_network(polygon, network_type, None)
        for element in response["elements"]
    ]

    settings = _overpass._make_overpass_settings()
    way_filter = _overpass._get_network_filter(network_type)
    kinds = "|".join(RELATION_TYPES)
    routes = "|".join(ROUTE_RELATIONS)
    ferries = "|".join(FERRY_ROUTES)
    for coords in _overpass._make_overpass_polygon_coord_strs(polygon):
        query = (
            f"{settings};way{way_filter}(poly:{coords!r})->.w;"
            f'way["route"~"^({ferries})$"]["highway"!~"."](poly:{coords!r})->.f;'
            f'(rel(bw.w)["type"~"^({kinds})(:|$)"];'
            f'rel(bw.w)["type"="route"]["route"~"^({routes})$"];'
            f".f;node(w.f););out;"
        )
        elements += _overpass._overpass_request(OrderedDict(data=query))["elements"]

    # Large areas are downloaded in pieces that overlap.
    return list({(e["type"], e["id"]): e for e in elements}.values())


def get_osm_data_from_file(
    osm_file: str | Path,
    bbox: gpd.GeoDataFrame,
    analysis_crs: CRS,
    network_type: str = "all",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Read the OSM network within a bounding box from a local OSM file
    (.osm.pbf, .osm, .osm.bz2 ... anything osmium reads), e.g. a
    Geofabrik extract. No size limit and no network access.
    """

    _check_network_type(network_type)

    path = Path(osm_file)
    if not path.is_file():
        raise InputError(f"OSM file not found: {path}", guide="osm-data.md#getting-an-extract")

    keep_mode_tags()

    polygon = bbox.to_crs("EPSG:4326").union_all()
    polygon_buffered = _buffered(polygon, bbox)

    _check_file_covers(path, polygon)

    elements = _read_elements(path, polygon_buffered.bounds, network_type)
    if not any(element["type"] == "way" and not is_ferry(element["tags"])
               for element in elements):
        raise InputError(
            f"{path.name} has no {network_type!r} ways inside the bounding box. "
            "Check the file covers the area.",
            guide="osm-data.md#getting-an-extract",
        )

    return _network_from_elements(elements, polygon, polygon_buffered, network_type, analysis_crs)


def _check_file_covers(path: Path, polygon) -> None:
    """Refuse a file whose header says it doesn't reach the area."""
    try:
        box = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING).header().box()
    except RuntimeError as exc:
        raise InputError(f"Can't read {path.name} as an OSM file ({exc}).") from exc

    if not box.valid():  # many files don't record their extent
        return
    west, south = box.bottom_left.lon, box.bottom_left.lat
    east, north = box.top_right.lon, box.top_right.lat
    min_lon, min_lat, max_lon, max_lat = polygon.bounds
    if max_lon < west or min_lon > east or max_lat < south or min_lat > north:
        raise InputError(
            f"{path.name} covers lon {west:.3f}..{east:.3f}, lat {south:.3f}..{north:.3f}, "
            "which doesn't overlap the bounding box.",
            guide="osm-data.md#getting-an-extract",
        )


def _read_elements(path: Path, bounds, network_type: str) -> list[dict]:
    """
    The ways in `path` within `bounds` (filtered like the Overpass query
    for network_type) with their nodes, and the relations kept with them
    (see OSMSource), as Overpass JSON elements.
    """
    west, south, east, north = bounds

    def inside(lon: float, lat: float) -> bool:
        return west <= lon <= east and south <= lat <= north

    tagged_nodes: dict[int, dict[str, str]] = {}
    locations: dict[int, tuple[float, float]] = {}
    ways, relations = [], []
    cut_ways = 0
    extra_way_ids = iter(range(-1, -(10**15), -1))  # ids for pieces of cut ways

    # Only objects with a highway, node-routing or relation-type tag reach
    # Python; the location cache still sees every node, so ways get coordinates.
    processor = (
        osmium.FileProcessor(str(path))
        .with_locations()
        .with_filter(osmium.filter.KeyFilter("highway", "type", "route", *ROUTING_NODE_KEYS))
    )
    try:
        for obj in processor:
            if obj.is_node():
                if inside(obj.location.lon, obj.location.lat):
                    tags = {tag.k: tag.v for tag in obj.tags}
                    if any(key in tags for key in ROUTING_NODE_KEYS):
                        tagged_nodes[obj.id] = tags
                continue

            tags = {tag.k: tag.v for tag in obj.tags}
            if obj.is_relation():
                if _keeps_relation(tags):
                    relations.append({"type": "relation", "id": obj.id, "tags": tags, "members": [
                        {"type": {"n": "node", "w": "way", "r": "relation"}[m.type],
                         "ref": m.ref, "role": m.role} for m in obj.members]})
                continue
            if not obj.is_way():
                continue

            if not is_ferry(tags) and (
                "highway" not in tags or not _passes_osmnx_filter(tags, network_type)
            ):
                continue
            nodes = [(n.ref, (n.location.lon, n.location.lat)) if n.location.valid()
                     else (n.ref, None) for n in obj.nodes]
            if not any(loc and inside(*loc) for _, loc in nodes):
                continue

            # An extract can cut a way at its edge, leaving refs to nodes it
            # doesn't contain. Keep each unbroken run of known nodes.
            runs = _known_runs(nodes)
            if len(runs) != 1 or len(runs[0]) != len(nodes):
                cut_ways += 1
            for number, run in enumerate(runs):
                way_id = obj.id if number == 0 else next(extra_way_ids)
                ways.append({"type": "way", "id": way_id, "tags": tags,
                             "nodes": [ref for ref, _ in run]})
                locations.update(run)
    except RuntimeError as exc:
        raise InputError(f"Can't read {path.name} as an OSM file ({exc}).") from exc

    if cut_ways:
        log.warning("%d way(s) in %s reference nodes the file doesn't contain (cut at "
                    "the extract's edge); kept the parts it does contain. Use an "
                    "extract that fully covers the area to avoid gaps.", cut_ways, path.name)

    nodes = [
        {"type": "node", "id": ref, "lon": lon, "lat": lat, "tags": tagged_nodes.get(ref, {})}
        for ref, (lon, lat) in sorted(locations.items())
    ]
    return [*nodes, *sorted(ways, key=lambda way: way["id"]), *relations]


def _known_runs(nodes: list[tuple[int, tuple[float, float] | None]]) -> list[list]:
    """Split (ref, location-or-None) into runs of 2+ consecutive known nodes."""
    runs, current = [], []
    for ref, location in nodes:
        if location is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
        else:
            current.append((ref, location))
    if len(current) >= 2:
        runs.append(current)
    return runs
