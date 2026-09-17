import geopandas as gpd


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


def update_gdf_tags(
    gdf: gpd.GeoDataFrame,
    custom_column: str,
    tags_to_update: dict,
) -> gpd.GeoDataFrame:
    """
    Update OSM tags for custom network features.

    Args:
        gdf: GeoDataFrame containing network lines.
        custom_column: Column used to identify custom features.
        tags_to_update: Dictionary of tags and values to apply.

    Returns:
        GeoDataFrame with updated tags.
    """

    # Identify custom network features.
    mask = gdf[custom_column] == "yes"

    # Apply each requested tag to custom features.
    for tag, value in tags_to_update.items():
        gdf.loc[mask, tag] = value

    return gdf