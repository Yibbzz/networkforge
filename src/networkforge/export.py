import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd

from .errors import InputError
from .modes import mode_tag_keys


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
        raise InputError(
            "Point GeoDataFrame must have a CRS assigned."
        )

    if split_lines_combined_gdf.crs is None:
        raise InputError(
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
            raise InputError(
                f"Node GeoDataFrame is missing required column: {column}"
            )

    for column in required_edge_columns:
        if column not in split_lines_combined_gdf.columns:
            raise InputError(
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

    tag_columns = {
        "highway": "highway",
        "name": "name",
        "ref": "ref",
        "lanes": "lanes",
        "maxspeed": "maxspeed",
        "oneway": "oneway",
        "access": "access",
        "service": "service",
        "bridge": "bridge",
        "tunnel": "tunnel",
        "layer": "layer",
        "junction": "junction",
        "surface": "surface",
        "width": "width",
        "custom": "nf:custom",
    }

    # Access tags every routing mode depends on (motor_vehicle, foot,
    # bicycle, ...). Dropping them would silently lift restrictions.
    tag_columns.update({key: key for key in mode_tag_keys()})

    # Only columns that exist. Plain column lookups rather than
    # itertuples, which can't handle keys like "sidewalk:left".
    present_tags = [
        (column, osm_key)
        for column, osm_key in tag_columns.items()
        if column in edges_wgs84.columns
    ]
    tag_values = [edges_wgs84[column].tolist() for column, _ in present_tags]

    for way_id, (u, v, *values) in enumerate(
        zip(edges_wgs84["u"], edges_wgs84["v"], *tag_values, strict=True),
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
            ref=str(int(u)),
        )

        # End node
        ET.SubElement(
            way,
            "nd",
            ref=str(int(v)),
        )

        # ------------------------------------------------------------
        # Preserve routing-relevant OSM tags (plus custom provenance)
        # ------------------------------------------------------------

        for (_, osm_key), value in zip(present_tags, values, strict=True):

            # OSMnx can represent some values as lists.
            if isinstance(value, list):
                value = ";".join(map(str, value))

            if value is None or pd.isna(value):
                continue

            ET.SubElement(
                way,
                "tag",
                k=osm_key,
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
