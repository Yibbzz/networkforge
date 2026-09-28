"""
Getting the existing OSM network: from the Overpass API (small areas)
or from a local OSM file such as a Geofabrik .osm.pbf extract (any size).

Both return the same thing: nodes and edges GeoDataFrames in the
analysis CRS, built exactly as ox.graph_from_bbox builds them (500 m
buffer, largest component, cropped to the box), so a build from a local
extract matches a build from Overpass for the same area and data.
"""

import logging
import os
import tempfile
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmium
import osmnx as ox
import requests
from osmnx._errors import InsufficientResponseError, ResponseStatusCodeError
from pyproj import CRS

from .errors import InputError, OSMDownloadError
from .modes import _passes_osmnx_filter, keep_mode_tags
from .tags import NODE_TAGS

log = logging.getLogger(__name__)

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


def _to_gdfs(
    graph: nx.MultiDiGraph,
    analysis_crs: CRS,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    nodes, edges = ox.graph_to_gdfs(graph)
    return nodes.to_crs(analysis_crs), edges.to_crs(analysis_crs).reset_index()


def get_osm_data_from_bbox(
    bbox: gpd.GeoDataFrame,
    analysis_crs: CRS,
    network_type: str = "all",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Download an OSM network within a bounding box from the Overpass API."""

    _check_network_type(network_type)

    # Keep motor_vehicle / foot / bicycle etc., which OSMnx drops by
    # default, so access restrictions survive into the export.
    keep_mode_tags()

    # Otherwise OSMnx caches responses in ./cache wherever it's run from.
    configure_osmnx_cache()

    bbox_wgs84 = bbox.to_crs("EPSG:4326")

    west, south, east, north = bbox_wgs84.total_bounds

    try:
        graph = ox.graph_from_bbox(
            (west, south, east, north),
            network_type=network_type,
            simplify=False,
        )
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

    return _to_gdfs(graph, analysis_crs)


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
    utm = bbox.estimate_utm_crs()
    buffered = gpd.GeoSeries([polygon], crs="EPSG:4326").to_crs(utm).buffer(BUFFER_M)
    polygon_buffered = buffered.to_crs("EPSG:4326").iloc[0]

    _check_file_covers(path, polygon)

    with tempfile.TemporaryDirectory() as tmp:
        area_xml = Path(tmp) / "area.osm"
        way_count = _extract_area(path, polygon_buffered.bounds, network_type, area_xml)
        if way_count == 0:
            raise InputError(
                f"{path.name} has no {network_type!r} ways inside the bounding box. "
                "Check the file covers the area.",
                guide="osm-data.md#getting-an-extract",
            )
        graph_buffered = ox.graph_from_xml(
            area_xml,
            bidirectional=network_type in ox.settings.bidirectional_network_types,
            simplify=False,
            retain_all=True,
        )

    # The same steps as ox.graph_from_polygon (what graph_from_bbox uses).
    graph_buffered = ox.truncate.truncate_graph_polygon(graph_buffered, polygon_buffered)
    graph_buffered = ox.truncate.largest_component(graph_buffered)
    graph = ox.truncate.truncate_graph_polygon(graph_buffered, polygon)
    graph = ox.truncate.largest_component(graph)
    street_counts = ox.stats.count_streets_per_node(graph_buffered, nodes=graph.nodes)
    nx.set_node_attributes(graph, values=street_counts, name="street_count")

    return _to_gdfs(graph, analysis_crs)


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


def _extract_area(path: Path, bounds, network_type: str, out_xml: Path) -> int:
    """
    Copy the ways (filtered like the Overpass query for network_type)
    and nodes within `bounds` from `path` into a small OSM XML file
    OSMnx can load. Returns the number of ways copied.
    """
    west, south, east, north = bounds

    def inside(lon: float, lat: float) -> bool:
        return west <= lon <= east and south <= lat <= north

    tagged_nodes: dict[int, dict[str, str]] = {}
    locations: dict[int, tuple[float, float]] = {}
    ways = []
    cut_ways = 0
    extra_way_ids = iter(range(-1, -(10**15), -1))  # ids for pieces of cut ways

    # Only objects with a highway or node-routing tag reach Python; the
    # location cache still sees every node, so ways get coordinates.
    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.KeyFilter("highway", *NODE_TAGS))
    )
    try:
        for obj in processor:
            if obj.is_node():
                if inside(obj.location.lon, obj.location.lat):
                    tags = {tag.k: tag.v for tag in obj.tags if tag.k in NODE_TAGS}
                    if tags:
                        tagged_nodes[obj.id] = tags
                continue

            tags = {tag.k: tag.v for tag in obj.tags}
            if "highway" not in tags or not _passes_osmnx_filter(tags, network_type):
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
                ways.append((way_id, [ref for ref, _ in run], tags))
                locations.update(run)
    except RuntimeError as exc:
        raise InputError(f"Can't read {path.name} as an OSM file ({exc}).") from exc

    if cut_ways:
        log.warning("%d way(s) in %s reference nodes the file doesn't contain (cut at "
                    "the extract's edge); kept the parts it does contain. Use an "
                    "extract that fully covers the area to avoid gaps.", cut_ways, path.name)

    writer = osmium.SimpleWriter(str(out_xml), overwrite=True)
    try:
        for ref in sorted(locations):
            writer.add_node(osmium.osm.mutable.Node(
                id=ref, location=locations[ref], tags=tagged_nodes.get(ref, {}),
            ))
        for way_id, refs, tags in sorted(ways, key=lambda way: way[0]):
            writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=refs, tags=tags))
    finally:
        writer.close()

    return len(ways)


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
