import geopandas as gpd
import osmnx as ox

from networkforge.network import build_network
from networkforge.export import write_osm_xml


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
        return_source_osm=True,
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

    nodes, edges, osm_nodes, osm_edges = build_test_network(
        bbox_gdf,
        custom_data_gdf,
    )

    # ------------------------------------------------------------------
    # Inspect result
    # ------------------------------------------------------------------

    print("\nNetwork built successfully!")

    print(f"  OSM nodes:       {len(osm_nodes)}")
    print(f"  OSM edges:       {len(osm_edges)}")
    print(f"  Final nodes:     {len(nodes)}")
    print(f"  Final edges:     {len(edges)}")
    print(f"  Node CRS:        {nodes.crs}")
    print(f"  Edge CRS:        {edges.crs}")

    # ------------------------------------------------------------------
    # Save GeoPackage outputs for inspection in QGIS
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

    # ------------------------------------------------------------------
    # Export baseline OSM network
    # ------------------------------------------------------------------

    print("\nExporting baseline OSM network...")

    write_osm_xml(
        osm_nodes,
        osm_edges,
        "tests/data/baseline.osm",
    )

    # ------------------------------------------------------------------
    # Export NetworkForge network
    # ------------------------------------------------------------------

    print("Exporting NetworkForge network...")

    write_osm_xml(
        nodes,
        edges,
        "tests/data/custom_network.osm",
    )

    # ------------------------------------------------------------------
    # Validate OSM exports
    # ------------------------------------------------------------------

    print("\nValidating OSM exports...")

    baseline_graph = ox.graph_from_xml(
        "tests/data/baseline.osm",
        simplify=False,
    )

    custom_graph = ox.graph_from_xml(
        "tests/data/custom_network.osm",
        simplify=False,
    )

    print(f"  Baseline graph nodes: {len(baseline_graph.nodes)}")
    print(f"  Baseline graph edges: {len(baseline_graph.edges)}")
    print(f"  Custom graph nodes:   {len(custom_graph.nodes)}")
    print(f"  Custom graph edges:   {len(custom_graph.edges)}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    print("\nOutput written:")
    print("  tests/data/test_output_nodes.gpkg")
    print("  tests/data/test_output_edges.gpkg")
    print("  tests/data/baseline.osm")
    print("  tests/data/custom_network.osm")


if __name__ == "__main__":
    main()