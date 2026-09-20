import geopandas as gpd
from pyproj import CRS


def get_analysis_crs(
    bbox: gpd.GeoDataFrame,
) -> CRS:
    """Determine the CRS to use for spatial analysis."""

    if bbox.crs is None:
        raise ValueError(
            "Bounding box must have a CRS assigned."
        )

    bbox_crs = CRS.from_user_input(bbox.crs)

    if bbox_crs.is_projected:
        return bbox_crs

    bbox_wgs84 = bbox.to_crs("EPSG:4326")

    centroid = bbox_wgs84.geometry.union_all().centroid

    longitude = centroid.x
    latitude = centroid.y

    zone = int((longitude + 180) // 6) + 1

    epsg = (
        32600 + zone
        if latitude >= 0
        else 32700 + zone
    )

    return CRS.from_epsg(epsg)

def convert_to_wgs84_and_add_xy(
    gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Convert a GeoDataFrame to WGS84 (EPSG:4326) and add
    x/y coordinate columns.

    Args:
        gdf: GeoDataFrame to convert.

    Returns:
        GeoDataFrame in EPSG:4326 with x and y columns.
    """

    # OSM uses WGS84 coordinates.
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    elif gdf.crs.to_string() != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")

    # Add longitude (x) and latitude (y) columns.
    gdf["x"] = gdf.geometry.x.round(7)
    gdf["y"] = gdf.geometry.y.round(7)

    return gdf