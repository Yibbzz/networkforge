import os
os.environ['USE_PYGEOS'] = '0'
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, MultiPoint, LineString, MultiLineString
from pyproj import CRS

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
    Split lines at points within a specified distance.

    buffer_distance is measured in the units of the projected
    analysis CRS, normally metres.
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
def remove_duplicates_and_combine_nodes(custom_points_gdf: gpd.GeoDataFrame, nodes_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Remove duplicate points from custom points and combine with nodes.

    Args:
        custom_points_gdf (gpd.GeoDataFrame): GeoDataFrame containing custom point geometries.
        nodes_gdf (gpd.GeoDataFrame): GeoDataFrame containing node geometries.

    Returns:
        gpd.GeoDataFrame: Combined GeoDataFrame of unique nodes and custom points.
    """

    validate_projected_crs(custom_points_gdf)
    validate_projected_crs(nodes_gdf)

    # Spatial join to see if any points intersect
    duplicates = gpd.sjoin(
        custom_points_gdf,
        nodes_gdf,
        how="inner",
        predicate="intersects",
    )

    # Check if 'index_left' exists in the result of the spatial join
    if 'index_left' in duplicates.columns:
        # Remove duplicates from custom_points
        custom_points_gdf_cleaned = custom_points_gdf.drop(duplicates['index_left'].unique())
    else:
        # If 'index_left' is not in columns, it means no duplicates were found
        # In this case, use custom_points_gdf as is
        custom_points_gdf_cleaned = custom_points_gdf

    # Concatenate the two GeoDataFrames, making sure the nodes_gdf index is not overwritten
    combined_points_gdf = pd.concat([nodes_gdf, custom_points_gdf_cleaned.reset_index(drop=True)])
    combined_points_gdf = combined_points_gdf.rename_axis("osmid", axis='index')

    return combined_points_gdf

#@log_time
def filter_split_lines(split_lines_combined_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Filter the combined OSM and custom lines GeoDataFrame to retain relevant lines.

    Args:
        split_lines_combined_gdf (gpd.GeoDataFrame): GeoDataFrame containing line geometries and attributes.

    Returns:
        gpd.GeoDataFrame: Filtered GeoDataFrame.
    """

    lines_list = []

    for i, row in split_lines_combined_gdf.iterrows():
        if (row['split'] == 'yes' and pd.notna(row['u'])) or pd.isna(row['u']) or (row['split'] == 'yes' and pd.notna(row['v'])) or pd.isna(row['v']):
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
def check_line_node_consistency(split_lines_combined_gdf: gpd.GeoDataFrame, combined_points_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Check for missing or duplicate node values in 'u' and 'v' columns and verify consistency.

    Args:
        split_lines_combined_gdf (gpd.GeoDataFrame): GeoDataFrame with line geometries ('u' and 'v' columns).
        combined_points_gdf (gpd.GeoDataFrame): GeoDataFrame with point geometries (index to be checked against).

    Returns:
        gpd.GeoDataFrame: Cleaned GeoDataFrame with line geometries.
    """

    # Drop rows with NaNs in 'u' or 'v'
    split_lines_combined_gdf = split_lines_combined_gdf.dropna(subset=['u', 'v'])

    # Drop rows with duplicate 'u' and 'v' values
    split_lines_combined_gdf = split_lines_combined_gdf[split_lines_combined_gdf['u'] != split_lines_combined_gdf['v']]

    # Convert 'u' and 'v' columns and the index to sets
    u_values = set(split_lines_combined_gdf['u'])
    v_values = set(split_lines_combined_gdf['v'])
    index_values = set(combined_points_gdf.index)

    # Find values in 'u' and 'v' that are not in the index
    not_in_index_u = u_values - index_values
    not_in_index_v = v_values - index_values

    if not_in_index_u:
        print("Values in 'u' not in index:", not_in_index_u)
    if not_in_index_v:
        print("Values in 'v' not in index:", not_in_index_v)
    if not not_in_index_u and not not_in_index_v:
        print('All u and v values are in the node index')

    return split_lines_combined_gdf