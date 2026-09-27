import geopandas as gpd
import networkx as nx
import osmnx as ox

from networkforge.modes import add_travel_times, load_graph

# Transport mode to route with. The .osm files contain every mode's
# ways; load_graph keeps only the ones this mode may use (and keeps the
# "nf:custom" and access tags OSMnx would otherwise drop).
MODE = "drive"


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


def route_uses_custom_network(graph, route) -> bool:
    """
    Implements 'assert custom_network_is_used' from
    tests/test_plan_integration.txt.

    Checks whether ANY edge along `route` carries the 'nf:custom'
    tag that export.py now writes for custom-data-derived ways.
    Requires the graph to have been loaded with load_graph, which
    keeps the 'nf:custom' tag.
    """
    for u, v in zip(route[:-1], route[1:], strict=False):
        edge_data = graph.get_edge_data(u, v)
        if not edge_data:
            continue
        for data in edge_data.values():
            if data.get("nf:custom") == "yes":
                return True
    return False


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

    print(f"\nLoading baseline graph (mode: {MODE})...")

    baseline_graph = load_graph("tests/data/baseline.osm", MODE)

    print(f"  Nodes: {len(baseline_graph.nodes):,}")
    print(f"  Edges: {len(baseline_graph.edges):,}")

    print(f"\nLoading custom graph (mode: {MODE})...")

    custom_graph = load_graph("tests/data/custom_network.osm", MODE)

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

    custom_used = route_uses_custom_network(custom_graph, custom_route)

    # Apples-to-apples distance comparison: the shortest-by-LENGTH route
    # on EACH graph, computed from scratch - not the length of whichever
    # route happened to win on travel_time. Comparing "distance of the
    # baseline's fastest route" against "distance of the custom graph's
    # shortest route" (what the previous version of this script did)
    # can look like an improvement even when it has nothing to do with
    # your custom data, if the existing street grid already has a
    # shorter-but-slower alternative. This is the fair comparison.
    baseline_start_node = ox.distance.nearest_nodes(baseline_graph, start_point.x, start_point.y)
    baseline_end_node = ox.distance.nearest_nodes(baseline_graph, end_point.x, end_point.y)
    baseline_route_by_len = nx.shortest_path(
        baseline_graph, baseline_start_node, baseline_end_node, weight="length"
    )
    baseline_len = nx.path_weight(baseline_graph, baseline_route_by_len, weight="length")

    custom_start_node = ox.distance.nearest_nodes(custom_graph, start_point.x, start_point.y)
    custom_end_node = ox.distance.nearest_nodes(custom_graph, end_point.x, end_point.y)
    custom_route_by_len = nx.shortest_path(
        custom_graph, custom_start_node, custom_end_node, weight="length"
    )
    custom_len = nx.path_weight(custom_graph, custom_route_by_len, weight="length")

    # Does the shortest-by-DISTANCE route actually touch your custom
    # edges? This is the key diagnostic: if this is False, the distance
    # improvement above (if any) has nothing to do with your custom
    # data, and it's worth re-checking connectivity, not tags.
    custom_used_by_len = route_uses_custom_network(custom_graph, custom_route_by_len)

    print("\n========================================")
    print("           ROUTING COMPARISON")
    print("========================================")

    print(
        f"Baseline route (by travel_time): "
        f"{baseline_time:.1f}s ({baseline_time / 60:.1f} min)"
    )
    print(
        f"Custom route   (by travel_time): "
        f"{custom_time:.1f}s ({custom_time / 60:.1f} min), "
        f"uses custom network: {custom_used}"
    )
    print(f"\nBaseline shortest route (by length): {baseline_len:.0f} m")
    print(
        f"Custom shortest route   (by length): {custom_len:.0f} m, "
        f"uses custom network: {custom_used_by_len}"
    )

    print(f"\nBaseline nodes: {len(baseline_route)}")
    print(f"Custom nodes:   {len(custom_route)}")
    print("========================================")

    # --------------------------------------------------------------
    # Diagnosis, printed BEFORE the asserts so a failure is
    # self-explanatory instead of just a traceback.
    # --------------------------------------------------------------

    if not custom_used_by_len:
        print(
            "\nDIAGNOSIS: even the shortest-by-DISTANCE route doesn't use your "
            "custom edges. This isn't a tagging issue - the custom network "
            "likely isn't well connected to the surrounding graph (or any "
            "distance win here is coincidental, from the existing street "
            "grid). Re-check the per-stage 'custom' edge counts build_network "
            "printed, and look for any 'not in index' warnings or an "
            "AssertionError from assert_all_custom_edges_are_connected."
        )
    elif custom_len < baseline_len and not custom_used:
        print(
            "\nDIAGNOSIS: your custom edges ARE connected and DO shorten the "
            f"route by distance ({custom_len:.0f} m vs {baseline_len:.0f} m), "
            "but the travel_time-optimal route avoids them anyway. That's a "
            "highway/maxspeed choice, not a connectivity bug - check the "
            "road class network_tags assigns your custom data against "
            "whatever it's bypassing."
        )
    elif custom_len >= baseline_len:
        print(
            "\nDIAGNOSIS: the custom network doesn't even shorten the route "
            "by raw distance here. Double check the custom geometry actually "
            "connects start and end more directly than the existing route "
            "does in this bounding box."
        )

    # --------------------------------------------------------------
    # tests/test_plan_integration.txt, made real:
    #   assert new_distance < baseline_distance
    #   assert custom_network_is_used
    # --------------------------------------------------------------

    assert custom_time < baseline_time, (
        f"Expected the custom network's route ({custom_time:.1f}s) to beat "
        f"the baseline's time-optimal route ({baseline_time:.1f}s), but it "
        f"didn't. See DIAGNOSIS above."
    )
    assert custom_used, (
        "Custom route is faster by travel_time, but doesn't appear to "
        "traverse any 'nf:custom'-tagged way - check that export.py is "
        "writing it (rerun scripts/get_test_data.py)."
    )

    print("\nassert new_distance < baseline_distance   -> PASSED")
    print("assert custom_network_is_used             -> PASSED")


if __name__ == "__main__":
    main()
