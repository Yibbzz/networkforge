import geopandas as gpd

from networkforge.network import build_network


def build_test_network(
    bbox_gdf: gpd.GeoDataFrame,
    custom_data_gdf: gpd.GeoDataFrame,
):
    """Build a simple drivable test network."""

    test_tags = {
        "highway": "residential",
        "maxspeed": "30",
        "lanes": "2",
        "oneway": "no",
        "access": "yes",
    }

    return build_network(
        bbox_gdf,
        custom_data_gdf,
        test_tags,
    )


def main():
    # ------------------------------------------------------------------
    # Load test data
    # ------------------------------------------------------------------

    bbox_gdf = gpd.read_file(
        "tests/data/extent.geojson"
    )

    custom_data_gdf = gpd.read_file(
        "tests/data/custom_road_test.geojson"
    )

    print("Loaded test data:")
    print(f"  Bounding box: {len(bbox_gdf)} feature(s)")
    print(f"  Custom roads: {len(custom_data_gdf)} feature(s)")
    print(f"  Bbox CRS:     {bbox_gdf.crs}")
    print(f"  Custom CRS:   {custom_data_gdf.crs}")

    # ------------------------------------------------------------------
    # Build network
    # ------------------------------------------------------------------

    print("\nBuilding network...")

    nodes, edges = build_test_network(
        bbox_gdf,
        custom_data_gdf,
    )

    # ------------------------------------------------------------------
    # Inspect result
    # ------------------------------------------------------------------

    print("\nNetwork built successfully!")

    print(f"  Nodes: {len(nodes)}")
    print(f"  Edges: {len(edges)}")
    print(f"  Node CRS: {nodes.crs}")
    print(f"  Edge CRS: {edges.crs}")

    print("\nNodes:")
    print(nodes.head())

    print("\nEdges:")
    print(edges.head())

    # ------------------------------------------------------------------
    # Save result so it can be inspected in QGIS
    # ------------------------------------------------------------------

    nodes.to_file(
        "tests/data/test_output_nodes.gpkg",
        layer="nodes",
        driver="GPKG",
    )

    edges.to_file(
        "tests/data/test_output_edges.gpkg",
        layer="edges",
        driver="GPKG",
    )

    print("\nOutput written:")
    print("  tests/data/test_output_nodes.gpkg")
    print("  tests/data/test_output_edges.gpkg")


if __name__ == "__main__":
    main()
