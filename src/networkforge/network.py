import geopandas as gpd
import pandas as pd
from pyproj import CRS

from .osm import (
    NETWORK_TYPES,
    configure_osmnx_cache,
    get_osm_data_from_bbox,
)
from .projection import get_analysis_crs
from .topology import (
    assign_point_ids_to_lines,
    check_line_node_consistency,
    create_points_from_gdf,
    filter_split_lines,
    remove_duplicates_and_combine_nodes,
    snap_crossings_to_network,
    snap_line_vertices_to_network,
    split_lines_with_buffered_points,
    update_and_finalize_lines_gdf,
    validate_user_osm_intersection,
)
from .validation import (
    assert_all_custom_edges_are_connected,
    assert_all_edges_have_valid_nodes,
    assert_custom_lines_unbroken,
    assert_no_u_equals_v,
    check_custom_tags,
    resolve_custom_tags,
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
    Fill in default OSM tags for custom network features.

    Only missing values are filled, so tags a feature brought with
    it (per-feature properties in the custom data) are kept.

    Args:
        gdf: GeoDataFrame containing network lines.
        custom_column: Column used to identify custom features.
        tags_to_update: Dictionary of default tags and values.

    Returns:
        GeoDataFrame with updated tags.
    """

    # Identify custom network features.
    mask = gdf[custom_column] == "yes"

    # Fill each default tag where a custom feature has no value.
    for tag, value in tags_to_update.items():
        if tag not in gdf.columns:
            gdf[tag] = None
        gdf.loc[mask & gdf[tag].isna(), tag] = value

    return gdf

def _count_custom(gdf: gpd.GeoDataFrame) -> int:
    """How many rows are tagged as custom, for debug logging."""
    if "custom" not in gdf.columns:
        return 0
    return int((gdf["custom"] == "yes").sum())

def build_network(
    bbox_gdf: gpd.GeoDataFrame,
    custom_data_gdf: gpd.GeoDataFrame,
    network_tags: dict[str, str],
    network_type: str = "all",
    return_source_osm: bool = False,
    snap_tolerance: float = 1.0,
    strict: bool = True,
) -> tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
] | tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
]:

    print("\n========================================")
    print("       NetworkForge Network Build")
    print("========================================")

    # =========================================================
    # 1. Configure OSMnx
    # =========================================================

    print("\n[1/14] Configuring OSMnx...")

    if network_type not in NETWORK_TYPES:
        raise ValueError(
            f"Unknown network_type {network_type!r}. "
            f"Choose one of: {', '.join(NETWORK_TYPES)}"
        )

    configure_osmnx_cache()

    analysis_crs = get_analysis_crs(bbox_gdf)

    print(f"      Analysis CRS: {analysis_crs}")
    print(f"      Network type: {network_type}")
    print(f"      Snap tolerance: {snap_tolerance} (CRS units, normally metres)")

    # Project custom data into the analysis CRS.
    custom_data_gdf = custom_data_gdf.to_crs(analysis_crs)

    print(f"      Custom features: {len(custom_data_gdf):,}")

    # Per-feature tags win; network_tags fill the gaps. Checked here,
    # before the slow download, so bad tags fail fast.
    print("      Checking custom feature tags...")

    custom_data_gdf = resolve_custom_tags(custom_data_gdf, network_tags)
    check_custom_tags(custom_data_gdf, network_type, strict=strict)

    # =========================================================
    # 2. Get the existing OSM network
    # =========================================================

    print("\n[2/14] Downloading OSM network...")

    nodes_gdf, edges_gdf = get_osm_data_from_bbox(
        bbox_gdf,
        analysis_crs,
        network_type=network_type,
    )

    print(f"      OSM nodes: {len(nodes_gdf):,}")
    print(f"      OSM edges: {len(edges_gdf):,}")
    print(f"      CRS: {edges_gdf.crs}")

    # =========================================================
    # 3. Combine OSM and custom network
    # =========================================================

    print("\n[3/14] Combining OSM and custom network...")

    # Custom vertices within snap_tolerance of the OSM network are moved
    # exactly onto it (nearest node, else nearest edge), so the line
    # joins the network itself rather than a point next to it.
    custom_data_gdf = snap_line_vertices_to_network(
        custom_data_gdf.explode(index_parts=False),
        nodes_gdf,
        edges_gdf,
        snap_tolerance,
    )

    if custom_data_gdf.empty:
        raise ValueError(
            f"Every custom line is shorter than snap_tolerance ({snap_tolerance}) "
            "and collapsed onto a single existing node - nothing to add."
        )

    combined_gdf = combine_custom_lines_with_osm_edges(
        custom_data_gdf,
        edges_gdf,
        analysis_crs,
    )

    print(f"      Combined lines: {len(combined_gdf):,} "
          f"({_count_custom(combined_gdf):,} custom)")

    # =========================================================
    # 4. Validate the custom network
    # =========================================================

    print("\n[4/14] Validating custom network intersections...")

    validate_user_osm_intersection(
        edges_gdf,
        custom_data_gdf,
        buffer_distance=snap_tolerance,
    )

    print("      \u2713 Custom network intersects existing OSM network")

    # =========================================================
    # 5. Find network topology points
    # =========================================================

    print("\n[5/14] Finding network topology points...")

    custom_points_gdf = create_points_from_gdf(
        combined_gdf
    )

    # Crossings near an OSM node are moved onto it before splitting.
    custom_points_gdf = snap_crossings_to_network(
        custom_points_gdf,
        custom_data_gdf,
        nodes_gdf,
        edges_gdf,
        snap_tolerance,
    )

    print(f"      Topology points: {len(custom_points_gdf):,}")

    # =========================================================
    # 6. Split lines at topology points
    # =========================================================

    print("\n[6/14] Splitting lines at topology points...")

    split_lines_gdf = split_lines_with_buffered_points(
        combined_gdf,
        custom_points_gdf,
        buffer_distance=snap_tolerance,
    )

    print(f"      Split lines: {len(split_lines_gdf):,} "
          f"({_count_custom(split_lines_gdf):,} custom)")

    # =========================================================
    # 7. Combine OSM nodes and custom nodes
    # =========================================================

    print("\n[7/14] Combining OSM and custom nodes...")

    combined_points_gdf = remove_duplicates_and_combine_nodes(
        custom_points_gdf,
        nodes_gdf,
        buffer_distance=snap_tolerance,
    )

    print(f"      Combined nodes: {len(combined_points_gdf):,} "
          f"({len(combined_points_gdf) - len(nodes_gdf):,} new, "
          f"vs {len(custom_points_gdf):,} candidate custom points before dedup)")

    # =========================================================
    # 8. Select lines requiring node assignment
    # =========================================================

    print("\n[8/14] Selecting lines requiring node assignment...")

    osm_split_lines_gdf = filter_split_lines(
        split_lines_gdf
    )

    print(f"      Lines requiring assignment: {len(osm_split_lines_gdf):,} "
          f"({_count_custom(osm_split_lines_gdf):,} custom)")

    # =========================================================
    # 9. Assign node IDs to line endpoints
    # =========================================================

    print("\n[9/14] Assigning node IDs to line endpoints...")

    # Must match the dedup tolerance in step 7: a custom point removed
    # there (within snap_tolerance of an OSM node) is replaced by that
    # node, so line endpoints need to find it at the same distance -
    # otherwise the segment is dropped and the custom line gets a gap.
    updated_lines = assign_point_ids_to_lines(
        osm_split_lines_gdf,
        combined_points_gdf,
        buffer_distance=snap_tolerance,
    )

    dropped = len(osm_split_lines_gdf) - len(updated_lines)
    print(f"      Updated lines: {len(updated_lines):,} "
          f"({_count_custom(updated_lines):,} custom, "
          f"{dropped:,} dropped as self-loops or unmatched)")

    # =========================================================
    # 10. Finalise the network edges
    # =========================================================

    print("\n[10/14] Finalising network edges...")

    final_lines_gdf = update_and_finalize_lines_gdf(
        split_lines_gdf,
        updated_lines,
    )

    print(f"       Final edges: {len(final_lines_gdf):,} "
          f"({_count_custom(final_lines_gdf):,} custom)")

    # =========================================================
    # 11. Validate topology
    # =========================================================

    print("\n[11/14] Validating network topology...")

    final_lines_gdf = check_line_node_consistency(
        final_lines_gdf,
        combined_points_gdf,
        strict=strict,
    )

    print(f"       Valid edges: {len(final_lines_gdf):,} "
          f"({_count_custom(final_lines_gdf):,} custom)")

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

    print("\n[13/14] Applying default OSM tags...")

    final_lines_gdf = update_gdf_tags(
        final_lines_gdf,
        "custom",
        network_tags,
    )

    print(f"       Defaults (per-feature tags take priority): {network_tags}")

    # =========================================================
    # 14. Final structural validation
    # =========================================================

    print("\n[14/14] Running structural validation...")

    try:
        assert_all_edges_have_valid_nodes(final_lines_gdf, combined_points_gdf)
        assert_no_u_equals_v(final_lines_gdf)
        assert_all_custom_edges_are_connected(final_lines_gdf)
        assert_custom_lines_unbroken(
            final_lines_gdf,
            len(custom_data_gdf.explode(index_parts=True)),
        )
        print("      \u2713 all_edges_have_valid_nodes")
        print("      \u2713 no_u_equals_v")
        print("      \u2713 all_custom_edges_are_connected")
        print("      \u2713 custom_lines_unbroken")
    except AssertionError as exc:
        if strict:
            raise
        print(f"      WARNING (strict=False, continuing anyway): {exc}")

    print("\nNetwork build complete!")

    print("\n========================================")
    print("              Summary")
    print("========================================")
    print(f"Nodes: {len(combined_points_gdf):,}")
    print(f"Edges: {len(final_lines_gdf):,} "
          f"({_count_custom(final_lines_gdf):,} custom)")
    print(f"CRS:   {final_lines_gdf.crs}")
    print("========================================\n")

    if return_source_osm:
        return (
            combined_points_gdf,
            final_lines_gdf,
            nodes_gdf,
            edges_gdf,
        )

    return (
        combined_points_gdf,
        final_lines_gdf,
    )
