import geopandas as gpd
import pandas as pd
from pyproj import CRS

from .projection import (
    get_analysis_crs, 
    convert_to_wgs84_and_add_xy
    )

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

def combine_custom_lines_with_osm_edges(
    custom: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    crs: str | CRS,
) -> gpd.GeoDataFrame:
    """
    Combine the user's custom network lines with OSM network edges.
    """

    # Split MultiLineStrings into individual LineStrings.
    custom = custom.explode(index_parts=True)

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
        crs=crs,
    )
    return combined_gdf

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

def build_network(
    bbox_gdf: gpd.GeoDataFrame,
    custom_data_gdf: gpd.GeoDataFrame,
    network_tags: dict,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:

    print("\n========================================")
    print("       NetworkForge Network Build")
    print("========================================")

    # =========================================================
    # 1. Configure OSMnx
    # =========================================================

    print("\n[1/14] Configuring OSMnx...")

    configure_osmnx_cache()

    analysis_crs = get_analysis_crs(bbox_gdf)

    print(f"      Analysis CRS: {analysis_crs}")

    # Project custom data into the analysis CRS.
    custom_data_gdf = custom_data_gdf.to_crs(analysis_crs)

    print(f"      Custom features: {len(custom_data_gdf):,}")

    # =========================================================
    # 2. Get the existing OSM network
    # =========================================================

    print("\n[2/14] Downloading OSM network...")

    nodes_gdf, edges_gdf = get_osm_data_from_bbox(
        bbox_gdf,
        analysis_crs,
    )

    print(f"      OSM nodes: {len(nodes_gdf):,}")
    print(f"      OSM edges: {len(edges_gdf):,}")
    print(f"      CRS: {edges_gdf.crs}")

    # =========================================================
    # 3. Combine OSM and custom network
    # =========================================================

    print("\n[3/14] Combining OSM and custom network...")

    combined_gdf = combine_custom_lines_with_osm_edges(
        custom_data_gdf,
        edges_gdf,
        analysis_crs,
    )

    print(f"      Combined lines: {len(combined_gdf):,}")

    # =========================================================
    # 4. Validate the custom network
    # =========================================================

    print("\n[4/14] Validating custom network intersections...")

    validate_user_osm_intersection(
        edges_gdf,
        custom_data_gdf,
    )

    print("      ✓ Custom network intersects existing OSM network")

    # =========================================================
    # 5. Find network topology points
    # =========================================================

    print("\n[5/14] Finding network topology points...")

    custom_points_gdf = create_points_from_gdf(
        combined_gdf
    )

    print(f"      Topology points: {len(custom_points_gdf):,}")

    # =========================================================
    # 6. Split lines at topology points
    # =========================================================

    print("\n[6/14] Splitting lines at topology points...")

    split_lines_gdf = split_lines_with_buffered_points(
        combined_gdf,
        custom_points_gdf,
    )

    print(f"      Split lines: {len(split_lines_gdf):,}")

    # =========================================================
    # 7. Combine OSM nodes and custom nodes
    # =========================================================

    print("\n[7/14] Combining OSM and custom nodes...")

    combined_points_gdf = remove_duplicates_and_combine_nodes(
        custom_points_gdf,
        nodes_gdf,
    )

    print(f"      Combined nodes: {len(combined_points_gdf):,}")

    # =========================================================
    # 8. Select lines requiring node assignment
    # =========================================================

    print("\n[8/14] Selecting lines requiring node assignment...")

    osm_split_lines_gdf = filter_split_lines(
        split_lines_gdf
    )

    print(f"      Lines requiring assignment: {len(osm_split_lines_gdf):,}")

    # =========================================================
    # 9. Assign node IDs to line endpoints
    # =========================================================

    print("\n[9/14] Assigning node IDs to line endpoints...")

    updated_lines = assign_point_ids_to_lines(
        osm_split_lines_gdf,
        combined_points_gdf,
    )

    print(f"      Updated lines: {len(updated_lines):,}")

    # =========================================================
    # 10. Finalise the network edges
    # =========================================================

    print("\n[10/14] Finalising network edges...")

    final_lines_gdf = update_and_finalize_lines_gdf(
        split_lines_gdf,
        updated_lines,
    )

    print(f"       Final edges: {len(final_lines_gdf):,}")

    # =========================================================
    # 11. Validate topology
    # =========================================================

    print("\n[11/14] Validating network topology...")

    final_lines_gdf = check_line_node_consistency(
        final_lines_gdf,
        combined_points_gdf,
    )

    print(f"       Valid edges: {len(final_lines_gdf):,}")

    # =========================================================
    # 12. Validate CRS consistency
    # =========================================================

    print("\n[12/14] Validating CRS consistency...")

    if combined_points_gdf.crs != final_lines_gdf.crs:
        raise ValueError(
            "Nodes and edges must use the same CRS."
        )

    print(f"       CRS: {final_lines_gdf.crs}")

    # =========================================================
    # 13. Apply OSM tags
    # =========================================================

    print("\n[13/14] Applying OSM tags...")

    final_lines_gdf = update_gdf_tags(
        final_lines_gdf,
        "custom",
        network_tags,
    )

    print(f"       Tags applied: {network_tags}")

    # =========================================================
    # 14. Return the finished network
    # =========================================================

    print("\n[14/14] Network build complete!")

    print("\n========================================")
    print("              Summary")
    print("========================================")
    print(f"Nodes: {len(combined_points_gdf):,}")
    print(f"Edges: {len(final_lines_gdf):,}")
    print(f"CRS:   {final_lines_gdf.crs}")
    print("========================================\n")

    return combined_points_gdf, final_lines_gdf