import geopandas as gpd
from pyproj import CRS

from .errors import InputError


def get_analysis_crs(
    bbox: gpd.GeoDataFrame,
) -> CRS:
    """Determine the CRS to use for spatial analysis."""

    if bbox.crs is None:
        raise InputError(
            "Bounding box must have a CRS assigned."
        )

    bbox_crs = CRS.from_user_input(bbox.crs)

    if bbox_crs.is_projected and _is_local(bbox_crs):
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

def _is_local(crs: CRS) -> bool:
    """
    True for a projected CRS made for one region (a UTM zone, a national
    grid), where a metre on the map is a metre on the ground. Not for
    world-wide projections such as Web Mercator (EPSG:3857, the default
    of many web maps), which stretches distances by 1/cos(latitude):
    lengths and the snap distance would be wrong by 80% in Scotland.
    """
    area = crs.area_of_use
    if area is None:
        return True  # a custom CRS: trust it
    return (area.east - area.west) < 30


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
