import os
os.environ['USE_PYGEOS'] = '0'
import geopandas as gpd
import osmnx as ox


def configure_osmnx_cache():
    """
    Configures the osmnx cache folder to a persistent folder with non-root permissions
    """
    # Get the directory of the current file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Define the cache folder relative to the current directory
    cache_folder = os.path.join(current_dir, "../temp/osmnx_cache")
    
    # Ensure the cache directory exists
    os.makedirs(cache_folder, exist_ok=True)
    
    # Set the osmnx cache folder
    ox.settings.cache_folder = cache_folder

def get_osm_data_from_bbox(
    bbox: gpd.GeoDataFrame,
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

    return nodes, edges.reset_index()