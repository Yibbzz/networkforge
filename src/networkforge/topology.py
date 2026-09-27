import os

os.environ['USE_PYGEOS'] = '0'
import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point

# A point this close to a line (projected CRS units) counts as on it -
# covers floating point error after projecting a point onto a line.
ON_LINE_TOLERANCE = 1e-6


def validate_projected_crs(gdf: gpd.GeoDataFrame) -> None:
    """
    Ensure a GeoDataFrame has a projected CRS suitable for
    distance and buffer operations.
    """

    if gdf.crs is None:
        raise ValueError(
            "GeoDataFrame must have a CRS assigned."
        )

    crs = CRS.from_user_input(gdf.crs)

    if not crs.is_projected:
        raise ValueError(
            f"GeoDataFrame must use a projected CRS for topology operations. "
            f"Received: {crs}"
        )

def validate_user_osm_intersection(
    lines_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 1.0,
) -> None:
    """
    Ensure that user data intersects the OSM network.

    buffer_distance is measured in the units of the projected
    analysis CRS, normally metres.
    """

    validate_projected_crs(lines_gdf)
    validate_projected_crs(points_gdf)

    buffered = points_gdf.copy()
    buffered["geometry"] = buffered.geometry.buffer(buffer_distance)

    sjoin_result = gpd.sjoin(
        lines_gdf,
        buffered,
        how="inner",
        predicate="intersects",
    )

    if sjoin_result.empty:
        raise ValueError(
            "User data does not intersect OSM network "
            "in the selected bounding box."
        )

def snap_points_to_nodes(
    points_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Move every point within `tolerance` of an OSM node exactly onto
    the nearest such node. Points further away are left as they are.

    Snapping BEFORE lines are split means every split happens at an
    exact node position, so later steps never have to guess which
    nearby node a line end was meant to join.
    """

    validate_projected_crs(points_gdf)
    validate_projected_crs(nodes_gdf)

    snapped = points_gdf.reset_index(drop=True)
    if snapped.empty or nodes_gdf.empty:
        return snapped

    node_geoms = gpd.GeoDataFrame(geometry=nodes_gdf.geometry.values, crs=nodes_gdf.crs)

    nearest = gpd.sjoin_nearest(
        snapped[["geometry"]],
        node_geoms,
        how="inner",
        max_distance=tolerance,
    )
    # Equidistant nodes produce several rows per point: keep one.
    nearest = nearest[~nearest.index.duplicated(keep="first")]

    snapped.loc[nearest.index, "geometry"] = (
        node_geoms.geometry.iloc[nearest["index_right"]].values
    )

    return snapped


def snap_points_to_edges(
    points_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Move every point within `tolerance` of an OSM edge onto the
    closest position ON the nearest such edge. Points already on an
    edge (e.g. crossings) don't move.
    """

    validate_projected_crs(points_gdf)
    validate_projected_crs(edges_gdf)

    snapped = points_gdf.reset_index(drop=True)
    if snapped.empty or edges_gdf.empty:
        return snapped

    edge_geoms = gpd.GeoDataFrame(geometry=edges_gdf.geometry.values, crs=edges_gdf.crs)

    nearest = gpd.sjoin_nearest(
        snapped[["geometry"]],
        edge_geoms,
        how="inner",
        max_distance=tolerance,
    )
    nearest = nearest[~nearest.index.duplicated(keep="first")]

    edges = edge_geoms.geometry.iloc[nearest["index_right"]].values
    points = snapped.geometry.loc[nearest.index].values
    snapped.loc[nearest.index, "geometry"] = [
        edge.interpolate(edge.project(point)) for edge, point in zip(edges, points, strict=True)
    ]

    return snapped


def snap_points_to_network(
    points_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Snap points within `tolerance` onto the existing network: onto the
    nearest OSM node if one is in range, otherwise onto the nearest OSM
    edge. Points further away stay put.
    """
    near_node = snap_points_to_nodes(points_gdf, nodes_gdf, tolerance)
    return snap_points_to_edges(near_node, edges_gdf, tolerance)


def snap_line_vertices_to_network(
    lines_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Snap each vertex of each (single-part) line onto the existing
    network (see snap_points_to_network). Repeated vertices this
    creates are removed; a line that collapses to one point is dropped.
    """

    lines = lines_gdf.reset_index(drop=True)

    line_numbers, vertices = [], []
    for number, line in enumerate(lines.geometry):
        for coord in line.coords:
            line_numbers.append(number)
            vertices.append(Point(coord))

    points = gpd.GeoDataFrame({"line": line_numbers}, geometry=vertices, crs=lines.crs)
    points = snap_points_to_network(points, nodes_gdf, edges_gdf, tolerance)

    new_geometries = []
    for number in range(len(lines)):
        coords = []
        for point in points.geometry[points["line"] == number]:
            if not coords or coords[-1] != (point.x, point.y):
                coords.append((point.x, point.y))
        new_geometries.append(LineString(coords) if len(coords) >= 2 else None)

    lines["geometry"] = new_geometries
    collapsed = lines.geometry.isna()
    if collapsed.any():
        print(f"      WARNING: {int(collapsed.sum())} custom line(s) shorter than the "
              f"snap tolerance collapsed onto a single node and were dropped")

    return lines[~collapsed]


#@log_time
def create_points_from_gdf(
    lines_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Create points from intersections and vertices of custom lines.
    """

    validate_projected_crs(lines_gdf)

    intersection_points = []

    sindex = lines_gdf.sindex

    lines_with_nan = lines_gdf[lines_gdf["u"].isna()]

    for i, line1 in lines_with_nan.iterrows():
        possible_matches_index = list(
            sindex.intersection(line1.geometry.bounds)
        )

        possible_matches = lines_gdf.iloc[possible_matches_index]

        precise_matches = possible_matches[
            possible_matches.intersects(line1.geometry)
        ]

        for j, line2 in precise_matches.iterrows():
            if i != j:
                intersection = line1.geometry.intersection(
                    line2.geometry
                )

                if isinstance(intersection, Point):
                    intersection_points.append(intersection)

                elif isinstance(intersection, MultiPoint):
                    intersection_points.extend(intersection.geoms)

    for line in lines_with_nan.geometry:
        if isinstance(line, LineString):
            intersection_points.extend(
                Point(coord) for coord in line.coords
            )

        elif isinstance(line, MultiLineString):
            for part in line.geoms:
                intersection_points.extend(
                    Point(coord) for coord in part.coords
                )

    points_gdf = gpd.GeoDataFrame(
        geometry=intersection_points,
        crs=lines_gdf.crs,
    )

    return points_gdf.drop_duplicates().reset_index(drop=True)


#@log_time
def split_lines_with_buffered_points(
    lines_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 1.0,
) -> gpd.GeoDataFrame:
    """
    Split lines at points.

    Custom lines (u is NaN) are split at points within buffer_distance
    (projected CRS units, normally metres), so they bend to meet the
    snapped junction. Existing OSM lines are only split at points that
    lie ON them: splitting at a nearby point would bend the existing
    street sideways and can create shortcuts that don't exist.
    """

    validate_projected_crs(lines_gdf)
    validate_projected_crs(points_gdf)

    buffered_points_gdf = points_gdf.copy()
    buffered_points_gdf["geometry"] = (
        buffered_points_gdf.geometry.buffer(buffer_distance)
    )

    sjoin_lines = gpd.sjoin(
        lines_gdf,
        buffered_points_gdf,
        how="inner",
        predicate="intersects",
    )

    is_osm = sjoin_lines["u"].notna()
    distance = sjoin_lines.geometry.distance(
        points_gdf.geometry.loc[sjoin_lines["index_right"]].set_axis(sjoin_lines.index)
    )
    sjoin_lines = sjoin_lines[~is_osm | (distance <= ON_LINE_TOLERANCE)]

    sjoin_lines["id"] = sjoin_lines.index

    split_lines_result = []
    split_ids = []

    for line_id, group in sjoin_lines.groupby("id"):
        line = lines_gdf.loc[line_id, "geometry"]

        attributes = lines_gdf.loc[line_id].drop("geometry")

        split_points = [
            points_gdf.loc[idx, "geometry"]
            for idx in group["index_right"]
        ]

        split_points.sort(
            key=lambda point: line.project(point)
        )

        segments = (
            [line.coords[0]]
            + [(point.x, point.y) for point in split_points]
            + [line.coords[-1]]
        )

        for i in range(len(segments) - 1):
            new_line = LineString(
                [segments[i], segments[i + 1]]
            )

            new_line_attributes = attributes.copy()
            new_line_attributes["geometry"] = new_line
            new_line_attributes["split"] = "yes"

            split_lines_result.append(
                new_line_attributes
            )

        split_ids.append(line_id)

    split_lines_gdf = gpd.GeoDataFrame(
        split_lines_result,
        geometry="geometry",
        crs=lines_gdf.crs,
    )

    non_split_lines_gdf = lines_gdf.drop(split_ids)

    return pd.concat(
        [non_split_lines_gdf, split_lines_gdf],
        ignore_index=True,
    )


#@log_time
def remove_duplicates_and_combine_nodes(
    custom_points_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 1.0,
) -> gpd.GeoDataFrame:
    """
    Remove duplicate points from custom points and combine with nodes.
    Args:
        custom_points_gdf (gpd.GeoDataFrame): GeoDataFrame containing custom point geometries.
        nodes_gdf (gpd.GeoDataFrame): GeoDataFrame containing node geometries.
        buffer_distance (float): distance (in the projected CRS's units,
            normally metres) within which a custom point is considered
            a duplicate of an existing node.

    Returns:
        gpd.GeoDataFrame: Combined GeoDataFrame of unique nodes and custom points.
    """

    validate_projected_crs(custom_points_gdf)
    validate_projected_crs(nodes_gdf)

    # Buffer the custom points (not the nodes) so a match means "this
    # custom point falls within buffer_distance of an existing node" -
    # consistent with how buffering is used everywhere else in this module.
    buffered_custom_points = custom_points_gdf.copy()
    buffered_custom_points["geometry"] = buffered_custom_points.geometry.buffer(
        buffer_distance
    )

    duplicates = gpd.sjoin(
        buffered_custom_points,
        nodes_gdf,
        how="inner",
        predicate="intersects",
    )

    # IMPORTANT: in current geopandas (>=1.0), gpd.sjoin does NOT add an
    # 'index_left' column - the left frame's original index is kept as
    # the result's own index, and only the right frame's index is added,
    # as 'index_right'. Older geopandas versions did add 'index_left' in
    # some cases. Checking only for 'index_left' (as this function used
    # to) means the "duplicate found" branch below is dead code on any
    # current geopandas install: NO duplicate is ever removed, exact
    # match or not, and custom_points_gdf_cleaned silently falls back to
    # the unmodified custom_points_gdf every time. That's a much bigger
    # problem than a wrong buffer_distance - it means this function was
    # never actually deduplicating anything. Handle both conventions.
    if 'index_left' in duplicates.columns:
        duplicate_positions = duplicates['index_left'].unique()
    else:
        duplicate_positions = duplicates.index.unique()

    if len(duplicate_positions) > 0:
        # Remove duplicates from custom_points
        custom_points_gdf_cleaned = custom_points_gdf.drop(duplicate_positions)
    else:
        # No duplicates found - use custom_points_gdf as is
        custom_points_gdf_cleaned = custom_points_gdf

    # New nodes are numbered above every existing OSM id. Numbering from
    # 0 collided with real low OSM ids, giving duplicate <node> ids on
    # export that silently moved existing streets.
    custom_points_gdf_cleaned = custom_points_gdf_cleaned.reset_index(drop=True)
    first_new_id = int(nodes_gdf.index.max()) + 1 if len(nodes_gdf) else 1
    custom_points_gdf_cleaned.index = custom_points_gdf_cleaned.index + first_new_id

    # Concatenate the two GeoDataFrames, making sure the nodes_gdf index is not overwritten
    combined_points_gdf = pd.concat([nodes_gdf, custom_points_gdf_cleaned])
    combined_points_gdf = combined_points_gdf.rename_axis("osmid", axis='index')

    return combined_points_gdf

#@log_time
def filter_split_lines(split_lines_combined_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Filter the combined OSM and custom lines GeoDataFrame to retain relevant lines.

    Args:
        split_lines_combined_gdf (gpd.GeoDataFrame): GeoDataFrame containing line
            geometries and attributes.

    Returns:
        gpd.GeoDataFrame: Filtered GeoDataFrame.
    """

    lines_list = []

    for i, row in split_lines_combined_gdf.iterrows():
        if row['split'] == 'yes' or pd.isna(row['u']) or pd.isna(row['v']):
            lines_list.append((i, row))

    # Creating a DataFrame from the list of tuples
    osm_split_lines_gdf = gpd.GeoDataFrame([row for index, row in lines_list],
                                           index=[index for index, row in lines_list],
                                           crs=split_lines_combined_gdf.crs)

    return osm_split_lines_gdf

def find_nearest_point_index(
    point: Point,
    points_gdf: gpd.GeoDataFrame,
    buffer_distance: float,
) -> int | None:
    """
    Find the nearest point within buffer_distance.

    buffer_distance is measured in the units of the projected
    analysis CRS, normally metres.
    """

    sindex = points_gdf.sindex

    possible_matches_index = list(
        sindex.intersection(
            point.buffer(buffer_distance).bounds
        )
    )

    possible_matches = points_gdf.iloc[
        possible_matches_index
    ]

    if possible_matches.empty:
        return None

    return possible_matches.distance(point).idxmin()

#@log_time
def assign_point_ids_to_lines(
    lines_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 0.1,
) -> gpd.GeoDataFrame:

    validate_projected_crs(lines_gdf)
    validate_projected_crs(points_gdf)

    lines_gdf = lines_gdf.copy()

    lines_gdf["start_point"] = lines_gdf.geometry.apply(
        lambda x: Point(x.coords[0])
    )

    lines_gdf["end_point"] = lines_gdf.geometry.apply(
        lambda x: Point(x.coords[-1])
    )

    lines_gdf["u"] = lines_gdf["start_point"].apply(
        lambda x: find_nearest_point_index(
            x,
            points_gdf,
            buffer_distance,
        )
    )

    lines_gdf["v"] = lines_gdf["end_point"].apply(
        lambda x: find_nearest_point_index(
            x,
            points_gdf,
            buffer_distance,
        )
    )

    updated_lines_gdf = lines_gdf.drop(
        columns=["start_point", "end_point"]
    )

    updated_lines_gdf = updated_lines_gdf[
        (updated_lines_gdf["u"] != updated_lines_gdf["v"])
        & updated_lines_gdf["u"].notna()
        & updated_lines_gdf["v"].notna()
    ]

    return updated_lines_gdf

#@log_time
def update_and_finalize_lines_gdf(
    original_gdf: gpd.GeoDataFrame,
    updated_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Update and finalize the lines GeoDataFrame.

    The GeoDataFrame remains in the projected analysis CRS.
    """

    validate_projected_crs(original_gdf)
    validate_projected_crs(updated_gdf)

    original_gdf = original_gdf.copy()

    original_gdf.update(updated_gdf)

    original_gdf = original_gdf.set_geometry("geometry")

    original_gdf["osmid"] = original_gdf.index + 1

    return original_gdf

#@log_time
def check_line_node_consistency(
    split_lines_combined_gdf: gpd.GeoDataFrame,
    combined_points_gdf: gpd.GeoDataFrame,
    strict: bool = True,
) -> gpd.GeoDataFrame:
    """
    Clean up and validate 'u'/'v' node references.

    Always drops rows with missing (NaN) or self-referencing
    (u == v) node ids.

    Then checks that every remaining 'u'/'v' value actually exists
    in combined_points_gdf's index. This used to only be printed as
    a warning and NOT enforced, which meant a dangling reference
    (an edge pointing at a node id that was never written to the
    node table) could silently reach `write_osm_xml`, producing
    invalid OSM XML. Depending on how lenient the reader is, that
    edge - which might be exactly the custom edge you care about -
    can then simply vanish when the file is reloaded, with no error
    at all.

    Args:
        split_lines_combined_gdf: GeoDataFrame with line geometries
            ('u' and 'v' columns).
        combined_points_gdf: GeoDataFrame with point geometries
            (index checked against).
        strict: if True (default), a dangling u/v reference raises
            a ValueError immediately, so you find out at build time
            rather than after a silent, confusing routing result.
            If False, offending rows are dropped and a warning is
            printed instead - useful while iterating on messy data.

    Returns:
        gpd.GeoDataFrame: Cleaned GeoDataFrame with line geometries.
    """

    before = len(split_lines_combined_gdf)

    # Drop rows with NaNs in 'u' or 'v'
    split_lines_combined_gdf = split_lines_combined_gdf.dropna(subset=['u', 'v'])

    # Drop self-loops (u == v)
    split_lines_combined_gdf = split_lines_combined_gdf[
        split_lines_combined_gdf['u'] != split_lines_combined_gdf['v']
    ]

    index_values = set(combined_points_gdf.index)

    valid_mask = (
        split_lines_combined_gdf['u'].isin(index_values)
        & split_lines_combined_gdf['v'].isin(index_values)
    )

    if not valid_mask.all():
        bad_rows = split_lines_combined_gdf.loc[~valid_mask]
        bad_u = set(bad_rows['u']) - index_values
        bad_v = set(bad_rows['v']) - index_values

        message = (
            f"{len(bad_rows)} edge(s) reference node ids that are not in the "
            f"node table. Missing 'u' values: {bad_u or 'none'}. "
            f"Missing 'v' values: {bad_v or 'none'}."
        )

        if 'custom' in bad_rows.columns and (bad_rows['custom'] == 'yes').any():
            message += " Some of these are your custom-tagged edges."

        if strict:
            raise ValueError(message)

        print("WARNING:", message)
        split_lines_combined_gdf = split_lines_combined_gdf.loc[valid_mask]
    else:
        print('      All u and v values are in the node index')

    after = len(split_lines_combined_gdf)
    if after != before:
        print(f"      check_line_node_consistency: {before} -> {after} edges "
              f"({before - after} dropped)")

    return split_lines_combined_gdf
