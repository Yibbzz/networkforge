import os
os.environ['USE_PYGEOS'] = '0'
import geopandas as gpd
import osmnx as ox
from pyproj import CRS
from .projection import get_analysis_crs


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