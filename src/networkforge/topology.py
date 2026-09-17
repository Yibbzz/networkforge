import os
os.environ['USE_PYGEOS'] = '0'
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, MultiPoint, LineString, MultiLineString
import warnings


def validate_user_osm_intersection(lines_gdf: gpd.GeoDataFrame,
                                   points_gdf: gpd.GeoDataFrame) -> None:
    """
    Ensures that user data actually intersects OSM network.
    Raises an error if no valid intersections exist.
    """

    buffered = points_gdf.copy()
    buffered["geometry"] = buffered.geometry.buffer(1e-5)

    sjoin_result = gpd.sjoin(
        lines_gdf,
        buffered,
        how="inner",
        predicate="intersects"
    )

    if sjoin_result.empty:
        raise ValueError(
            "User data does not intersect with OSM network in the selected bounding box"
        )

#@log_time
def create_points_from_gdf(lines_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Create points from the intersection of lines in a GeoDataFrame.

    Args:
        lines_gdf (gpd.GeoDataFrame): GeoDataFrame containing line geometries.

    Returns:
        gpd.GeoDataFrame: GeoDataFrame containing points at intersections.
    """

    intersection_points = []

    # Create a spatial index for lines_gdf
    sindex = lines_gdf.sindex

    # Filter lines with NaN in 'u'
    lines_with_nan = lines_gdf[lines_gdf['u'].isna()]

    # Check for intersections using the spatial index
    for i, line1 in lines_with_nan.iterrows():
        possible_matches_index = list(sindex.intersection(line1['geometry'].bounds))
        possible_matches = lines_gdf.iloc[possible_matches_index]
        precise_matches = possible_matches[possible_matches.intersects(line1['geometry'])]

        for j, line2 in precise_matches.iterrows():
            if i != j:
                intersection = line1['geometry'].intersection(line2['geometry'])
                if isinstance(intersection, (Point, MultiPoint)):
                    if intersection.geom_type == 'Point':
                        intersection_points.append(intersection)
                    else:  # MultiPoint
                        intersection_points.extend([pt for pt in intersection])

    # Create points at vertices of lines with NaN in 'u'
    for line in lines_with_nan['geometry']:
        if isinstance(line, LineString):
            intersection_points.extend([Point(coord) for coord in line.coords])
        elif isinstance(line, MultiLineString):
            for part in line:
                intersection_points.extend([Point(coord) for coord in part.coords])

    # Convert to GeoDataFrame and remove duplicates
    points_gdf = gpd.GeoDataFrame(geometry=intersection_points)
    unique_points_gdf = points_gdf.drop_duplicates().reset_index(drop=True)
    
    unique_points_gdf.crs = lines_gdf.crs
    return unique_points_gdf


# Splits every line that intersects with a point, at the point they intersect with the point
#@log_time
def split_lines_with_buffered_points(lines_gdf: gpd.GeoDataFrame, points_gdf: gpd.GeoDataFrame, buffer_distance: float = 1e-5) -> gpd.GeoDataFrame:
    """
    Split lines at points buffered by a specified distance.

    Args:
        lines_gdf (gpd.GeoDataFrame): GeoDataFrame containing line geometries.
        points_gdf (gpd.GeoDataFrame): GeoDataFrame containing point geometries.
        buffer_distance (float): Distance to buffer points before splitting lines.

    Returns:
        gpd.GeoDataFrame: GeoDataFrame with lines split at buffered points.
    """

    # Create a new GeoDataFrame with buffered points
    buffered_points_gdf = points_gdf.copy()
    buffered_points_gdf['geometry'] = points_gdf['geometry'].buffer(buffer_distance)

    # Perform spatial join
    sjoin_lines = gpd.sjoin(lines_gdf, buffered_points_gdf, how='inner', op='intersects')
    sjoin_lines['id'] = sjoin_lines.index

    split_lines_result = []
    split_ids = []

    for line_id, group in sjoin_lines.groupby('id'):
        line = lines_gdf.loc[line_id, 'geometry']
        attributes = lines_gdf.loc[line_id].drop('geometry')  # Remove geometry to keep attributes

        split_points = [buffered_points_gdf.loc[idx, 'geometry'].centroid for idx in group['index_right']]

        # If no internal intersections, continue without appending
        if not split_points:
            continue

        # Sort the split points by their distance along the line
        split_points.sort(key=lambda pt: line.project(pt))

        # Create line segments from points and add attributes
        segments = [line.coords[0]] + [(pt.x, pt.y) for pt in split_points] + [line.coords[-1]]
        for i in range(len(segments) - 1):
            new_line = LineString([segments[i], segments[i+1]])
            new_line_attributes = attributes.copy()  # Copy the original attributes
            new_line_attributes['geometry'] = new_line  # Set the new geometry
            new_line_attributes['split'] = 'yes'  # Identifier if the line has been split
            split_lines_result.append(new_line_attributes)

        split_ids.append(line_id)

    # Create a new GeoDataFrame from the split_lines_result list
    split_lines_gdf = gpd.GeoDataFrame(split_lines_result, geometry='geometry', crs=lines_gdf.crs)

    # Remove the split lines from the original lines_gdf
    non_split_lines_gdf = lines_gdf.drop(split_ids)

    # Concatenate non-split lines with split lines
    final_lines_gdf = pd.concat([non_split_lines_gdf, split_lines_gdf], ignore_index=True)

    return final_lines_gdf

    
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

    # Spatial join to see if any points intersect
    duplicates = gpd.sjoin(custom_points_gdf, nodes_gdf, how="inner", predicate="intersects")

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

def find_nearest_point_index(point: Point, points_gdf: gpd.GeoDataFrame, buffer_distance: float) -> int:
    """
    Find the index of the nearest point within a specified buffer distance.

    Args:
        point (Point): The point to find the nearest neighbor for.
        points_gdf (gpd.GeoDataFrame): GeoDataFrame containing point geometries.
        buffer_distance (float): Buffer distance to search for nearest points.

    Returns:
        int: Index of the nearest point, or None if no point is found.
    """

    # Spatial index
    sindex = points_gdf.sindex
    # Find possible matches within the buffer distance
    possible_matches_index = list(sindex.intersection(point.buffer(buffer_distance).bounds))
    possible_matches = points_gdf.iloc[possible_matches_index]

    # Find the nearest point
    # Ignore the warnings about using a geo crs
    if not possible_matches.empty:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            nearest_point_index = possible_matches.distance(point).idxmin()        
        return nearest_point_index

    return None

#@log_time
def assign_point_ids_to_lines(lines_gdf: gpd.GeoDataFrame, points_gdf: gpd.GeoDataFrame, buffer_distance: float = 0.1) -> gpd.GeoDataFrame:
    """
    Assign point IDs to lines by matching start and end points within a buffer.

    Args:
        lines_gdf (gpd.GeoDataFrame): GeoDataFrame containing line geometries.
        points_gdf (gpd.GeoDataFrame): GeoDataFrame containing point geometries.
        buffer_distance (float): Buffer distance for matching points to lines.

    Returns:
        gpd.GeoDataFrame: Updated GeoDataFrame with point IDs assigned to lines.
    """

    # Identify Start and End Points of Lines
    lines_gdf['start_point'] = lines_gdf['geometry'].apply(lambda x: Point(x.coords[0]))
    lines_gdf['end_point'] = lines_gdf['geometry'].apply(lambda x: Point(x.coords[-1]))

    # Manually match start and end points with the points within the buffer
    lines_gdf['u'] = lines_gdf['start_point'].apply(lambda x: find_nearest_point_index(x, points_gdf, buffer_distance))
    lines_gdf['v'] = lines_gdf['end_point'].apply(lambda x: find_nearest_point_index(x, points_gdf, buffer_distance))

    # Drop temporary columns
    updated_lines_gdf = lines_gdf.drop(columns=['start_point', 'end_point'])

    # Filter out lines with duplicate 'u' and 'v' values or NaN in 'u' or 'v'
    updated_lines_gdf = updated_lines_gdf[
        (updated_lines_gdf['u'] != updated_lines_gdf['v']) &
        updated_lines_gdf['u'].notna() &
        updated_lines_gdf['v'].notna()
    ]

    # Set the CRS for the updated GeoDataFrame
    updated_lines_gdf = updated_lines_gdf.set_crs(lines_gdf.crs)

    return updated_lines_gdf

#@log_time
def update_and_finalize_lines_gdf(original_gdf: gpd.GeoDataFrame, updated_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Update and finalize the lines GeoDataFrame with new data and add 'x' and 'y' columns.

    Args:
        original_gdf (gpd.GeoDataFrame): The original GeoDataFrame to be updated.
        updated_gdf (gpd.GeoDataFrame): The GeoDataFrame containing updated data.

    Returns:
        gpd.GeoDataFrame: Updated GeoDataFrame with unique 'osmid'.
    """

    # Update the original GeoDataFrame with the updated data
    original_gdf.update(updated_gdf)

    # Reset the geometry column
    original_gdf = original_gdf.set_geometry('geometry')

    # Create a unique 'osmid' column
    original_gdf['osmid'] = original_gdf.index + 1
    
    # Check and set CRS if not set
    if original_gdf.crs is None:
        original_gdf.set_crs("EPSG:4326", inplace=True)

    # Convert to wgs84 if not already in that CRS
    if original_gdf.crs.to_string() != "EPSG:4326":
        original_gdf = original_gdf.to_crs("EPSG:4326")

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