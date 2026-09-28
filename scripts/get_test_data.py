import logging

import geopandas as gpd
import osmnx as ox

from networkforge.export import write_osm, write_osm_xml
from networkforge.network import build_network


def build_test_network(
    bbox_gdf: gpd.GeoDataFrame,
    custom_data_gdf: gpd.GeoDataFrame,
):
    """Build the demo network: every custom line becomes a primary road."""

    return build_network(
        bbox_gdf,
        custom_data_gdf,
        preset="primary_road",
        network_tags={"maxspeed": "70", "lanes": "4", "access": "yes"},
        network_type="all",
        return_source_osm=True,
    )


def main():
    # networkforge reports progress through logging; show it on screen.
    logging.basicConfig(level=logging.INFO, format="%(message)s")

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

    # The same network as PBF, the compressed format routers such as
    # GraphHopper and Valhalla read.
    write_osm(
        nodes,
        edges,
        "tests/data/custom_network.osm.pbf",
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
    print("  tests/data/baseline.osm")
    print("  tests/data/custom_network.osm")
    print("  tests/data/custom_network.osm.pbf")


if __name__ == "__main__":
    main()
