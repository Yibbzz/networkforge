import os
os.environ['USE_PYGEOS'] = '0'
import geopandas as gpd
import pandas as pd
import xml.etree.ElementTree as ET

def write_osm_xml(
    combined_points_gdf: gpd.GeoDataFrame,
    split_lines_combined_gdf: gpd.GeoDataFrame,
    output_file_path: str,
):
    """
    Write nodes and ways from GeoDataFrames to an OSM XML file.

    Input GeoDataFrames should use the analysis CRS. Both are converted
    to WGS84 (EPSG:4326) for OSM export.

    Args:
        combined_points_gdf: GeoDataFrame containing point geometries.
        split_lines_combined_gdf: GeoDataFrame containing line geometries.
        output_file_path: Path to the output OSM XML file.
    """

    # ------------------------------------------------------------------
    # Convert data to WGS84 for OSM export
    # ------------------------------------------------------------------

    if combined_points_gdf.crs is None:
        raise ValueError("Point GeoDataFrame must have a CRS assigned.")

    if split_lines_combined_gdf.crs is None:
        raise ValueError("Line GeoDataFrame must have a CRS assigned.")

    combined_points_gdf = combined_points_gdf.to_crs("EPSG:4326")
    split_lines_combined_gdf = split_lines_combined_gdf.to_crs("EPSG:4326")

    # Add longitude (x) and latitude (y) columns.
    combined_points_gdf["x"] = combined_points_gdf.geometry.x.round(7)
    combined_points_gdf["y"] = combined_points_gdf.geometry.y.round(7)

    # ------------------------------------------------------------------
    # Calculate total bounds
    # ------------------------------------------------------------------

    points_bounds = combined_points_gdf.total_bounds
    lines_bounds = split_lines_combined_gdf.total_bounds

    total_bounds = [
        round(min(points_bounds[0], lines_bounds[0]), 7),  # minx
        round(min(points_bounds[1], lines_bounds[1]), 7),  # miny
        round(max(points_bounds[2], lines_bounds[2]), 7),  # maxx
        round(max(points_bounds[3], lines_bounds[3]), 7),  # maxy
    ]

    # ------------------------------------------------------------------
    # Create OSM XML
    # ------------------------------------------------------------------

    root = ET.Element(
        "osm",
        version="0.6",
        generator="CustomGen",
    )

    ET.SubElement(
        root,
        "bounds",
        minlat=str(total_bounds[1]),
        minlon=str(total_bounds[0]),
        maxlat=str(total_bounds[3]),
        maxlon=str(total_bounds[2]),
    )

    # Add nodes
    for row in combined_points_gdf.itertuples():
        ET.SubElement(
            root,
            "node",
            id=str(row.Index),
            lat=str(row.y),
            lon=str(row.x),
        )

    # Add ways
    standard_tags = [
        "highway",
        "width",
        "oneway",
        "maxspeed",
        "bridge",
        "lanes",
        "access",
        "service",
    ]

    for row in split_lines_combined_gdf.itertuples():

        way = ET.SubElement(
            root,
            "way",
            id=str(int(row.osmid)),
        )

        ET.SubElement(
            way,
            "nd",
            ref=str(int(row.u)),
        )

        ET.SubElement(
            way,
            "nd",
            ref=str(int(row.v)),
        )

        for tag in standard_tags:
            value = getattr(row, tag, None)

            if pd.notna(value):
                ET.SubElement(
                    way,
                    "tag",
                    k=tag,
                    v=str(value),
                )

    # ------------------------------------------------------------------
    # Write XML file
    # ------------------------------------------------------------------

    tree = ET.ElementTree(root)

    tree.write(
        output_file_path,
        encoding="utf-8",
        xml_declaration=True,
    )