import os
import geopandas as gpd
import pandas as pd
import xml.etree.ElementTree as ET

def write_osm_xml(
    combined_points_gdf: gpd.GeoDataFrame,
    split_lines_combined_gdf: gpd.GeoDataFrame,
    output_file_path: str,
):
    """
    Write a NetworkForge network to standard OSM 0.6 XML.

    Input GeoDataFrames should use the analysis CRS. Both are
    converted to WGS84 (EPSG:4326) for OSM export.

    Each graph edge is exported as a separate OSM way connecting
    its `u` and `v` nodes.

    Args:
        combined_points_gdf:
            Network nodes.

        split_lines_combined_gdf:
            Network edges. Must contain `u`, `v` and `osmid`
            columns.

        output_file_path:
            Path to the output OSM XML file.
    """

    # ================================================================
    # 1. Validate CRS
    # ================================================================

    if combined_points_gdf.crs is None:
        raise ValueError(
            "Point GeoDataFrame must have a CRS assigned."
        )

    if split_lines_combined_gdf.crs is None:
        raise ValueError(
            "Line GeoDataFrame must have a CRS assigned."
        )

    # ================================================================
    # 2. Validate required network columns
    # ================================================================

    required_node_columns = ["geometry"]

    required_edge_columns = [
        "u",
        "v",
    ]

    for column in required_node_columns:
        if column not in combined_points_gdf.columns:
            raise ValueError(
                f"Node GeoDataFrame is missing required column: {column}"
            )

    for column in required_edge_columns:
        if column not in split_lines_combined_gdf.columns:
            raise ValueError(
                f"Edge GeoDataFrame is missing required column: {column}"
            )

    # ================================================================
    # 3. Convert to WGS84
    # ================================================================

    nodes_wgs84 = combined_points_gdf.to_crs("EPSG:4326")
    edges_wgs84 = split_lines_combined_gdf.to_crs("EPSG:4326")

    # ================================================================
    # 4. Calculate bounds
    # ================================================================

    points_bounds = nodes_wgs84.total_bounds
    lines_bounds = edges_wgs84.total_bounds

    total_bounds = [
        round(min(points_bounds[0], lines_bounds[0]), 7),
        round(min(points_bounds[1], lines_bounds[1]), 7),
        round(max(points_bounds[2], lines_bounds[2]), 7),
        round(max(points_bounds[3], lines_bounds[3]), 7),
    ]

    # ================================================================
    # 5. Create OSM document
    # ================================================================

    root = ET.Element(
        "osm",
        version="0.6",
        generator="NetworkForge",
    )

    ET.SubElement(
        root,
        "bounds",
        minlat=str(total_bounds[1]),
        minlon=str(total_bounds[0]),
        maxlat=str(total_bounds[3]),
        maxlon=str(total_bounds[2]),
    )

    # ================================================================
    # 6. Write nodes
    # ================================================================

    for row in nodes_wgs84.itertuples():

        geometry = row.geometry

        ET.SubElement(
            root,
            "node",
            id=str(row.Index),
            lat=str(round(geometry.y, 7)),
            lon=str(round(geometry.x, 7)),
        )

    # ================================================================
    # 7. Write ways
    # ================================================================

    standard_tags = [
        "highway",
        "name",
        "ref",
        "lanes",
        "maxspeed",
        "oneway",
        "access",
        "service",
        "bridge",
        "tunnel",
        "junction",
        "surface",
        "width",
    ]

    for way_id, row in enumerate(
        edges_wgs84.itertuples(),
        start=1,
    ):

        # ------------------------------------------------------------
        # Each graph edge becomes its own OSM way.
        #
        # We deliberately generate a new unique ID rather than using
        # the original OSM `osmid`, because multiple graph edges can
        # originate from the same original OSM way.
        # ------------------------------------------------------------

        way = ET.SubElement(
            root,
            "way",
            id=str(way_id),
        )

        # Start node
        ET.SubElement(
            way,
            "nd",
            ref=str(int(row.u)),
        )

        # End node
        ET.SubElement(
            way,
            "nd",
            ref=str(int(row.v)),
        )

        # ------------------------------------------------------------
        # Preserve routing-relevant OSM tags
        # ------------------------------------------------------------

        for tag in standard_tags:

            value = getattr(row, tag, None)

            if pd.isna(value):
                continue

            # OSMnx can represent some values as lists.
            if isinstance(value, list):
                value = ";".join(map(str, value))

            ET.SubElement(
                way,
                "tag",
                k=tag,
                v=str(value),
            )

    # ================================================================
    # 8. Write XML
    # ================================================================

    tree = ET.ElementTree(root)

    tree.write(
        output_file_path,
        encoding="utf-8",
        xml_declaration=True,
    )