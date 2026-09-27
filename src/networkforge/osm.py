import os

os.environ['USE_PYGEOS'] = '0'
import geopandas as gpd
import osmnx as ox
from pyproj import CRS

from .modes import keep_mode_tags

# OSMnx's built-in network filters, applied in the Overpass query.
# "all" (the default) keeps every mode's ways plus their access tags,
# so one build can be routed for any mode later (see modes.py).
# Narrower types drop ways at download, and custom lines can then
# only be joined to what's left.
NETWORK_TYPES = ("all", "all_public", "bike", "drive", "drive_service", "walk")


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

def get_osm_data_from_bbox(
    bbox: gpd.GeoDataFrame,
    analysis_crs: CRS,
    network_type: str = "all",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Download an OSM network within a bounding box."""

    if network_type not in NETWORK_TYPES:
        raise ValueError(
            f"Unknown network_type {network_type!r}. "
            f"Choose one of: {', '.join(NETWORK_TYPES)}"
        )

    # Keep motor_vehicle / foot / bicycle etc., which OSMnx drops by
    # default, so access restrictions survive into the export.
    keep_mode_tags()

    bbox_wgs84 = bbox.to_crs("EPSG:4326")

    west, south, east, north = bbox_wgs84.total_bounds

    graph = ox.graph_from_bbox(
        (west, south, east, north),
        network_type=network_type,
        simplify=False,
    )

    nodes, edges = ox.graph_to_gdfs(graph)

    nodes = nodes.to_crs(analysis_crs)
    edges = edges.to_crs(analysis_crs)

    return nodes, edges.reset_index()
