import geopandas as gpd
import networkx as nx
import osmnx as ox


def load_points():
    start_gdf = gpd.read_file(
        "tests/data/start_point.geojson"
    )

    end_gdf = gpd.read_file(
        "tests/data/end_point.geojson"
    )

    if start_gdf.crs is None:
        raise ValueError("Start point has no CRS.")

    if end_gdf.crs is None:
        raise ValueError("End point has no CRS.")

    start_gdf = start_gdf.to_crs("EPSG:4326")
    end_gdf = end_gdf.to_crs("EPSG:4326")

    return start_gdf, end_gdf


def add_travel_times(graph):
    graph = ox.add_edge_speeds(graph)
    graph = ox.add_edge_travel_times(graph)

    return graph


def calculate_route(
    graph,
    start_point,
    end_point,
):
    start_node = ox.distance.nearest_nodes(
        graph,
        start_point.x,
        start_point.y,
    )

    end_node = ox.distance.nearest_nodes(
        graph,
        end_point.x,
        end_point.y,
    )

    route = nx.shortest_path(
        graph,
        start_node,
        end_node,
        weight="travel_time",
    )

    travel_time = nx.path_weight(
        graph,
        route,
        weight="travel_time",
    )

    return route, travel_time


def main():
    # --------------------------------------------------------------
    # Load start/end points
    # --------------------------------------------------------------

    start_gdf, end_gdf = load_points()

    start_point = start_gdf.geometry.iloc[0]
    end_point = end_gdf.geometry.iloc[0]

    print("Start/end points:")
    print(f"  Start: {start_point}")
    print(f"  End:   {end_point}")

    # --------------------------------------------------------------
    # Load graphs
    # --------------------------------------------------------------

    print("\nLoading baseline graph...")

    baseline_graph = ox.graph_from_xml(
        "tests/data/baseline.osm",
        simplify=False,
    )

    print(f"  Nodes: {len(baseline_graph.nodes):,}")
    print(f"  Edges: {len(baseline_graph.edges):,}")

    print("\nLoading custom graph...")

    custom_graph = ox.graph_from_xml(
        "tests/data/custom_network.osm",
        simplify=False,
    )

    print(f"  Nodes: {len(custom_graph.nodes):,}")
    print(f"  Edges: {len(custom_graph.edges):,}")

    # --------------------------------------------------------------
    # Add speeds and travel times
    # --------------------------------------------------------------

    print("\nCalculating edge speeds/travel times...")

    baseline_graph = add_travel_times(
        baseline_graph
    )

    custom_graph = add_travel_times(
        custom_graph
    )

    # --------------------------------------------------------------
    # Calculate routes
    # --------------------------------------------------------------

    print("\nCalculating baseline route...")

    baseline_route, baseline_time = calculate_route(
        baseline_graph,
        start_point,
        end_point,
    )

    print("\nCalculating custom route...")

    custom_route, custom_time = calculate_route(
        custom_graph,
        start_point,
        end_point,
    )

    # --------------------------------------------------------------
    # Results
    # --------------------------------------------------------------

    print("\n========================================")
    print("           ROUTING COMPARISON")
    print("========================================")

    print(
        f"Baseline route: "
        f"{baseline_time:.1f} seconds "
        f"({baseline_time / 60:.1f} minutes)"
    )

    print(
        f"Custom route:   "
        f"{custom_time:.1f} seconds "
        f"({custom_time / 60:.1f} minutes)"
    )

    print(f"\nBaseline nodes: {len(baseline_route)}")
    print(f"Custom nodes:   {len(custom_route)}")

    print("========================================")


if __name__ == "__main__":
    main()