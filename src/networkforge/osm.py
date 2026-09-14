import geopandas as gpd
import osmnx as ox


def get_osm_network(
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