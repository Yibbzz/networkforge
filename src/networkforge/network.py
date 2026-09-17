import geopandas as gpd
import pandas as pd

from .osm import (
    configure_osmnx_cache,
    get_osm_data_from_bbox,
)

from .topology import (
    validate_user_osm_intersection,
    create_points_from_gdf,
    split_lines_with_buffered_points,
    remove_duplicates_and_combine_nodes,
    filter_split_lines,
    assign_point_ids_to_lines,
    update_and_finalize_lines_gdf,
    check_line_node_consistency,
)

from .osm_conversion import (
    convert_to_wgs84_and_add_xy,
    update_gdf_tags,
)


def combine_custom_lines_with_osm_edges(
    custom: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Combine the user's custom network lines with OSM network edges.
    """

    # Split MultiLineStrings into individual LineStrings.
    custom = custom.explode(index_parts=True)

    # Ensure custom data uses WGS84.
    if custom.crs.to_string() != "EPSG:4326":
        custom = custom.to_crs("EPSG:4326")

    # Reset the OSM edge index before combining.
    edges_gdf_reset = edges_gdf.reset_index(drop=True)

    # Mark custom features so they can be identified later.
    custom["custom"] = "yes"

    # Combine OSM and custom network lines.
    combined_gdf = gpd.GeoDataFrame(
        pd.concat(
            [edges_gdf_reset, custom],
            ignore_index=True,
        ),
        crs="EPSG:4326",
    )

    return combined_gdf


def build_network(
    bbox_gdf: gpd.GeoDataFrame,
    custom_data_gdf: gpd.GeoDataFrame,
    network_tags: dict,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Build a connected network from OSM data and user-provided
    custom network features.

    This function builds the network but does not export it.

    Returns:
        Tuple containing:

        nodes_gdf:
            Final network nodes.

        lines_gdf:
            Final network edges.
    """

    # =========================================================
    # 1. Configure OSMnx
    # =========================================================
    # Set up the persistent OSMnx cache so downloaded OSM data
    # can be reused.
    configure_osmnx_cache()


    # =========================================================
    # 2. Get the existing OSM network
    # =========================================================
    # Download the OSM network within the supplied bounding box.
    #
    # We get two datasets:
    #   - nodes: existing OSM network nodes
    #   - edges: existing OSM network lines
    nodes_gdf, edges_gdf = get_osm_data_from_bbox(
        bbox_gdf
    )


    # =========================================================
    # 3. Combine OSM and custom network
    # =========================================================
    # Add the user's proposed network lines to the existing
    # OSM network.
    #
    # Custom features are marked with custom="yes" so that
    # they can be identified later in the pipeline.
    combined_gdf = combine_custom_lines_with_osm_edges(
        custom_data_gdf,
        edges_gdf,
    )


    # =========================================================
    # 4. Validate the custom network
    # =========================================================
    # Check that the user's proposed network actually
    # intersects the existing OSM network.
    #
    # If there is no valid intersection, the pipeline stops.
    validate_user_osm_intersection(
        combined_gdf,
        custom_data_gdf,
    )


    # =========================================================
    # 5. Find network topology points
    # =========================================================
    # Find intersections and vertices in the combined network
    # that need to become network nodes.
    custom_points_gdf = create_points_from_gdf(
        combined_gdf
    )


    # =========================================================
    # 6. Split lines at topology points
    # =========================================================
    # Split network lines wherever a topology point occurs.
    #
    # This creates individual edges between network nodes.
    split_lines_gdf = split_lines_with_buffered_points(
        combined_gdf,
        custom_points_gdf,
    )


    # =========================================================
    # 7. Combine OSM nodes and custom nodes
    # =========================================================
    # Combine the newly-created custom topology points with
    # the original OSM nodes.
    #
    # Points which already correspond to an OSM node are
    # removed to avoid duplicate nodes.
    combined_points_gdf = remove_duplicates_and_combine_nodes(
        custom_points_gdf,
        nodes_gdf,
    )


    # =========================================================
    # 8. Select lines requiring node assignment
    # =========================================================
    # Identify the lines that need their start and end nodes
    # assigned.
    osm_split_lines_gdf = filter_split_lines(
        split_lines_gdf
    )


    # =========================================================
    # 9. Assign node IDs to line endpoints
    # =========================================================
    # Match the start and end of every relevant line to the
    # nearest network node.
    #
    # The resulting u/v columns represent:
    #
    #       u -------- edge --------> v
    #
    updated_lines = assign_point_ids_to_lines(
        osm_split_lines_gdf,
        combined_points_gdf,
    )


    # =========================================================
    # 10. Finalise the network edges
    # =========================================================
    # Merge the updated u/v values back into the complete
    # split-line dataset and assign unique OSM IDs.
    final_lines_gdf = update_and_finalize_lines_gdf(
        split_lines_gdf,
        updated_lines,
    )


    # =========================================================
    # 11. Validate topology
    # =========================================================
    # Check that every edge references valid nodes and remove
    # invalid or duplicate u/v relationships.
    final_lines_gdf = check_line_node_consistency(
        final_lines_gdf,
        combined_points_gdf,
    )


    # =========================================================
    # 12. Prepare nodes for OSM
    # =========================================================
    # Convert the node coordinates to WGS84 and add the x/y
    # longitude/latitude values required by the OSM exporter.
    combined_points_gdf = convert_to_wgs84_and_add_xy(
        combined_points_gdf
    )


    # =========================================================
    # 13. Apply OSM tags
    # =========================================================
    # Apply the requested network tags to the user's custom
    # network features.
    final_lines_gdf = update_gdf_tags(
        final_lines_gdf,
        "custom",
        network_tags,
    )


    # =========================================================
    # 14. Return the finished network
    # =========================================================
    # The network is now completely built.
    #
    # Nothing is written to disk here. The caller decides
    # which export format to use.
    return combined_points_gdf, final_lines_gdf