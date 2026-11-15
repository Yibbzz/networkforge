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

    # Longitude 180 itself would otherwise be a zone 61, which doesn't exist.
    zone = min(int((longitude + 180) // 6) + 1, 60)

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
    Nor for one measured in feet: everything here is in metres.
    """
    if any(axis.unit_name != "metre" for axis in crs.axis_info):
        return False  # feet (US State Plane): lengths and the snap distance are metres
    area = crs.area_of_use
    if area is None:
        return True  # a custom CRS: trust it
    return (area.east - area.west) < 30
