"""
The NetworkForge pipeline: merge custom lines into the OSM network.

Progress is reported through the `logging` module (logger names start
with "networkforge") and, optionally, a progress callback. The library
never configures logging itself - call logging.basicConfig(level=
logging.INFO) in your script to see the build steps.
"""

import logging
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS

from .errors import InputError, NetworkIntegrityError
from .inputs import check_bbox, clean_custom_data
from .osm import (
    NETWORK_TYPES,
    SOURCE_ATTR,
    get_osm_data_from_bbox,
    get_osm_data_from_file,
)
from .presets import preset_tags
from .projection import get_analysis_crs
from .tags import PART_COLUMN
from .topology import (
    assign_point_ids_to_lines,
    check_line_node_consistency,
    create_points_from_gdf,
    filter_split_lines,
    remove_duplicates_and_combine_nodes,
    snap_crossings_to_network,
    snap_line_vertices_to_network,
    split_at_self_crossings,
    split_lines_with_buffered_points,
    update_and_finalize_lines_gdf,
    validate_user_osm_intersection,
)
from .validation import (
    assert_all_edges_have_valid_nodes,
    assert_custom_lines_unbroken,
    assert_no_u_equals_v,
    check_custom_tags,
    disconnected_custom_edges,
    resolve_custom_tags,
)

log = logging.getLogger(__name__)

# progress(step, total_steps, description) - called as each step starts.
ProgressCallback = Callable[[int, int, str], None]

TOTAL_STEPS = 13


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

    # Mark custom features so they can be identified later, and number
    # the lines so export can write each one as a single way.
    custom["custom"] = "yes"
    custom[PART_COLUMN] = np.arange(len(custom))

    # Combine OSM and custom network lines.
    combined_gdf = gpd.GeoDataFrame(
        pd.concat(
            [edges_gdf_reset, custom],
            ignore_index=True,
        ),
        crs=crs,
    )
    return combined_gdf

def _count_custom(gdf: gpd.GeoDataFrame) -> int:
    """How many rows are tagged as custom, for logging."""
    if "custom" not in gdf.columns:
        return 0
    return int((gdf["custom"] == "yes").sum())


def _warn_about_disconnected_features(
    edges_gdf: gpd.GeoDataFrame,
    custom_gdf: gpd.GeoDataFrame,
    snap_tolerance: float,
) -> None:
    """
    Warn, naming the features, about custom lines that ended up outside
    the main network (e.g. drawn away from every street). They stay in
    the output, but no router can reach them.

    custom_gdf: the snapped custom lines, indexed by feature id.
    """
    stranded = edges_gdf[disconnected_custom_edges(edges_gdf)]
    if stranded.empty:
        return

    # Edges don't carry their feature's id: find the custom line each
    # stranded edge was cut from by its midpoint.
    midpoints = shapely.line_interpolate_point(stranded.geometry.to_numpy(), 0.5, normalized=True)
    tree = shapely.STRtree(custom_gdf.geometry.to_numpy())
    _, line_pos = tree.query_nearest(midpoints, max_distance=snap_tolerance)
    features = list(dict.fromkeys(custom_gdf.index[np.unique(line_pos)]))

    log.warning("%d custom feature(s) don't connect to the rest of the network, so a "
                "router can never reach them (features %s). They are kept in the "
                "output; extend them to meet a street if they should connect.",
                len(features), ", ".join(map(str, features)), extra={"features": features})


def build_network(
    bbox_gdf: gpd.GeoDataFrame,
    custom_data_gdf: gpd.GeoDataFrame,
    network_tags: dict[str, str] | None = None,
    network_type: str = "all",
    return_source_osm: bool = False,
    snap_tolerance: float = 1.0,
    strict: bool = True,
    *,
    preset: str | None = None,
    overwrite_tags: bool = False,
    osm_source: str | Path | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
] | tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
]:
    """
    Download the OSM network in `bbox_gdf` and merge the custom lines
    into it, following OSM access and grade-separation rules.

    Tags (see docs/tagging-guide.md):
        Each custom feature's own attributes (highway, maxspeed, ...)
        are its tags. The blanket options add to them:
          preset         name of a ready-made tag set (presets.PRESETS)
          network_tags   extra tags; these win over the preset
          overwrite_tags False: the feature's own values win, the
                         blanket only fills gaps. True: the blanket
                         replaces feature values for every key it sets.

    Args:
        bbox_gdf: area to download; any CRS.
        custom_data_gdf: custom LineString/MultiLineString features; any CRS.
        network_type: OSMnx download filter. Keep "all" to route any mode later.
        return_source_osm: also return the untouched OSM nodes and edges
            (the baseline network for before/after comparisons).
        snap_tolerance: metres within which custom lines join the network.
        strict: raise on bad tags, unusable features or structural problems.
            False logs warnings and drops what can't be used instead.
            Custom lines that don't connect to the network only ever warn
            (and are kept), as long as at least one line reaches it.
        osm_source: a local OSM file (.osm.pbf, .osm, ...), e.g. a Geofabrik
            extract, to read the existing network from instead of the
            Overpass API. Required for boxes over MAX_OVERPASS_AREA_KM2
            (see docs/osm-data.md).
        progress: optional callback(step, total_steps, description).

    Returns:
        (nodes, edges), or (nodes, edges, osm_nodes, osm_edges) with
        return_source_osm. All in the projected analysis CRS.

    Raises:
        InputError (incl. InvalidTagsError, NoIntersectionError),
        OSMDownloadError, NetworkIntegrityError - see errors.py.
    """

    def step(number: int, description: str) -> None:
        log.info("[%d/%d] %s", number, TOTAL_STEPS, description)
        if progress is not None:
            progress(number, TOTAL_STEPS, description)

    # =========================================================
    # 1. Check inputs and tags (before the slow download)
    # =========================================================

    step(1, "Checking inputs and tags")

    if network_type not in NETWORK_TYPES:
        raise InputError(
            f"Unknown network_type {network_type!r}. "
            f"Choose one of: {', '.join(NETWORK_TYPES)}"
        )
    if not snap_tolerance > 0:
        raise InputError(f"snap_tolerance must be positive, got {snap_tolerance!r}")

    check_bbox(bbox_gdf, local_source=osm_source is not None)
    custom_data_gdf = clean_custom_data(custom_data_gdf, bbox_gdf, strict=strict)

    analysis_crs = get_analysis_crs(bbox_gdf)
    custom_data_gdf = custom_data_gdf.to_crs(analysis_crs)

    blanket_tags = {**(preset_tags(preset) if preset else {}), **(network_tags or {})}
    custom_data_gdf = resolve_custom_tags(custom_data_gdf, blanket_tags, overwrite=overwrite_tags)
    check_custom_tags(custom_data_gdf, network_type, strict=strict)

    log.info("Custom features: %d | analysis CRS: %s | network type: %s | "
             "snap tolerance: %s m", len(custom_data_gdf), analysis_crs.to_string(),
             network_type, snap_tolerance)
    if blanket_tags:
        log.info("Blanket tags (%s): %s",
                 "replacing feature values" if overwrite_tags else "filling gaps",
                 blanket_tags)

    # =========================================================
    # 2. Get the existing OSM network
    # =========================================================

    if osm_source is not None:
        step(2, f"Reading OSM network from {Path(osm_source).name}")
        nodes_gdf, edges_gdf = get_osm_data_from_file(
            osm_source,
            bbox_gdf,
            analysis_crs,
            network_type=network_type,
        )
    else:
        step(2, "Downloading OSM network")
        nodes_gdf, edges_gdf = get_osm_data_from_bbox(
            bbox_gdf,
            analysis_crs,
            network_type=network_type,
        )

    log.info("OSM network: %d nodes, %d edges", len(nodes_gdf), len(edges_gdf))

    # OSM as it is (all tags, turn restrictions): export writes the
    # existing network from this. Re-attached to the results at the end.
    source_data = edges_gdf.attrs.pop(SOURCE_ATTR, None)

    # =========================================================
    # 3. Combine OSM and custom network
    # =========================================================

    step(3, "Snapping custom lines and combining with the OSM network")

    # Custom vertices within snap_tolerance of the OSM network are moved
    # exactly onto it (nearest node, else nearest edge), so the line
    # joins the network itself rather than a point next to it.
    custom_parts_gdf = split_at_self_crossings(custom_data_gdf.explode(index_parts=False))
    custom_data_gdf = snap_line_vertices_to_network(
        custom_parts_gdf,
        nodes_gdf,
        edges_gdf,
        snap_tolerance,
    )

    if custom_data_gdf.empty:
        raise InputError(
            f"Every custom line is shorter than snap_tolerance ({snap_tolerance}) "
            "and collapsed onto a single existing node - nothing to add."
        )

    # The snapped lines are numbered by position; keep each one's feature id.
    custom_feature_ids = custom_parts_gdf.index[custom_data_gdf.index]

    combined_gdf = combine_custom_lines_with_osm_edges(
        custom_data_gdf,
        edges_gdf,
        analysis_crs,
    )

    # =========================================================
    # 4. Check the custom network reaches the OSM network
    # =========================================================

    step(4, "Checking custom lines reach the OSM network")

    validate_user_osm_intersection(
        edges_gdf,
        custom_data_gdf,
        buffer_distance=snap_tolerance,
    )

    # =========================================================
    # 5. Find network topology points
    # =========================================================

    step(5, "Finding junctions")

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

    log.debug("Topology points: %d", len(custom_points_gdf))

    # =========================================================
    # 6. Split lines at topology points
    # =========================================================

    step(6, "Splitting lines at junctions")

    split_lines_gdf = split_lines_with_buffered_points(
        combined_gdf,
        custom_points_gdf,
        buffer_distance=snap_tolerance,
    )

    log.debug("Split lines: %d (%d custom)", len(split_lines_gdf), _count_custom(split_lines_gdf))

    # =========================================================
    # 7. Combine OSM nodes and custom nodes
    # =========================================================

    step(7, "Combining OSM and new nodes")

    combined_points_gdf = remove_duplicates_and_combine_nodes(
        custom_points_gdf,
        nodes_gdf,
        buffer_distance=snap_tolerance,
    )

    log.debug("Combined nodes: %d (%d new)",
              len(combined_points_gdf), len(combined_points_gdf) - len(nodes_gdf))

    # =========================================================
    # 8. Select lines requiring node assignment
    # =========================================================

    step(8, "Selecting lines that need node ids")

    osm_split_lines_gdf = filter_split_lines(
        split_lines_gdf
    )

    # =========================================================
    # 9. Assign node IDs to line endpoints
    # =========================================================

    step(9, "Assigning node ids to line ends")

    # Must match the dedup tolerance in step 7: a custom point removed
    # there (within snap_tolerance of an OSM node) is replaced by that
    # node, so line endpoints need to find it at the same distance -
    # otherwise the segment is dropped and the custom line gets a gap.
    updated_lines = assign_point_ids_to_lines(
        osm_split_lines_gdf,
        combined_points_gdf,
        buffer_distance=snap_tolerance,
    )

    log.debug("Updated lines: %d (%d dropped as self-loops or unmatched)",
              len(updated_lines), len(osm_split_lines_gdf) - len(updated_lines))

    # =========================================================
    # 10. Finalise the network edges
    # =========================================================

    step(10, "Finalising edges")

    # Pieces left without two different end nodes (the zero-length piece
    # cut off where a line is split at its own end) must go: an existing
    # street's piece would otherwise keep the whole street's u and v, a
    # second copy of the street that bypasses the new junctions on it.
    not_edges = osm_split_lines_gdf.index.difference(updated_lines.index)

    final_lines_gdf = update_and_finalize_lines_gdf(
        split_lines_gdf.drop(not_edges),
        updated_lines,
    )

    # =========================================================
    # 11. Validate topology
    # =========================================================

    step(11, "Checking every edge references real nodes")

    final_lines_gdf = check_line_node_consistency(
        final_lines_gdf,
        combined_points_gdf,
        strict=strict,
    )

    # =========================================================
    # 12. Validate CRS consistency
    # =========================================================

    step(12, "Checking CRS consistency")

    if combined_points_gdf.crs != final_lines_gdf.crs:
        raise NetworkIntegrityError(
            "Nodes and edges must use the same CRS."
        )

    # =========================================================
    # 13. Final structural validation
    # =========================================================

    step(13, "Running structural checks")

    try:
        assert_all_edges_have_valid_nodes(final_lines_gdf, combined_points_gdf)
        assert_no_u_equals_v(final_lines_gdf)
        assert_custom_lines_unbroken(
            final_lines_gdf,
            len(custom_data_gdf.explode(index_parts=True)),
        )
    except NetworkIntegrityError as exc:
        if strict:
            raise
        log.warning("strict=False, continuing anyway: %s", exc)

    _warn_about_disconnected_features(
        final_lines_gdf,
        custom_data_gdf.set_axis(custom_feature_ids),
        snap_tolerance,
    )

    log.info("Network built: %d nodes, %d edges (%d custom)",
             len(combined_points_gdf), len(final_lines_gdf), _count_custom(final_lines_gdf))

    if source_data is not None:
        final_lines_gdf.attrs[SOURCE_ATTR] = source_data
        edges_gdf.attrs[SOURCE_ATTR] = source_data

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
