"""
Real data: Valhalla must route on NetworkForge's "before" file as it
routes on the OpenStreetMap extract the file was made from, and a
custom footbridge must change walking trips only.

Run with: uv run pytest -m network tests/live/test_valhalla_real_data.py -v

Downloads Geofabrik's Monaco extract (under 1 MB). The grid tests in
tests/valhalla prove the rules; this proves nothing is lost on real
data, with its turn restrictions, long ways and thousands of tags.
"""

import random
import urllib.error
import urllib.request

import geopandas as gpd
import osmium
import pytest
from shapely.geometry import LineString, box

from networkforge import build_network, write_gpkg, write_osm
from networkforge.validation import KNOWN_HIGHWAYS, KNOWN_TAG_KEYS

pytest.importorskip("valhalla", reason="pyvalhalla is not installed (uv sync installs it)")

from tests.valhalla.harness import Router  # noqa: E402

pytestmark = [pytest.mark.network, pytest.mark.valhalla]

EXTRACT_URL = "https://download.geofabrik.de/europe/monaco-latest.osm.pbf"
TRIPS = 30  # points; every pair is routed

# A footbridge across Port Hercule, from quay to quay.
FOOTBRIDGE = LineString([(7.4208, 43.7353), (7.4252, 43.7338)])


@pytest.fixture(scope="module")
def extract(tmp_path_factory):
    path = tmp_path_factory.mktemp("geofabrik") / "monaco.osm.pbf"
    try:
        urllib.request.urlretrieve(EXTRACT_URL, path)
    except urllib.error.URLError as exc:
        pytest.fail(f"Couldn't download {EXTRACT_URL}: {exc}")
    return path


@pytest.fixture(scope="module")
def routers(extract, tmp_path_factory):
    """Valhalla on the extract itself, and on NetworkForge's before and after files."""
    folder = tmp_path_factory.mktemp("monaco")
    header = osmium.io.Reader(str(extract), osmium.osm.osm_entity_bits.NOTHING).header().box()
    whole_file = gpd.GeoDataFrame(geometry=[box(
        header.bottom_left.lon - 0.01, header.bottom_left.lat - 0.01,
        header.top_right.lon + 0.01, header.top_right.lat + 0.01)], crs="EPSG:4326")
    custom = gpd.GeoDataFrame({"highway": ["footway"], "bridge": ["yes"], "layer": ["1"]},
                              geometry=[FOOTBRIDGE], crs="EPSG:4326")

    nodes, edges, osm_nodes, osm_edges = build_network(
        whole_file, custom, osm_source=extract, return_source_osm=True)
    write_osm(osm_nodes, osm_edges, folder / "before.osm.pbf")
    write_osm(nodes, edges, folder / "after.osm.pbf")

    return {
        "osm": Router.from_pbf(extract, folder / "osm"),
        "before": Router.from_pbf(folder / "before.osm.pbf", folder / "before"),
        "after": Router.from_pbf(folder / "after.osm.pbf", folder / "after"),
    }


@pytest.fixture(scope="module")
def points(routers):
    """Mid-points of ordinary streets, the same every run."""
    streets = sorted(
        (way_id, refs) for way_id, (refs, tags) in routers["before"].ways.items()
        if tags.get("highway") in ("residential", "tertiary", "secondary", "primary",
                                   "unclassified")
    )
    chosen = random.Random(1).sample(streets, TRIPS)
    return [routers["before"].node(refs[len(refs) // 2]) for _, refs in chosen]


def differing(a, b, tolerance=0.01, metres=0.0):
    """
    Share of trips whose distance differs by more than `tolerance` and
    by more than `metres` (or that exist in one matrix only).
    """
    pairs = [(x, y) for row_a, row_b in zip(a, b, strict=True)
             for x, y in zip(row_a, row_b, strict=True)]
    differ = sum(
        (x is None) != (y is None)
        or (x is not None and abs(x[0] - y[0]) > max(metres, tolerance * max(x[0], 1)))
        for x, y in pairs
    )
    return differ / len(pairs)


@pytest.mark.parametrize("costing", ["auto", "bus", "truck", "bicycle", "pedestrian"])
def test_before_routes_exactly_like_the_extract(routers, points, costing):
    """Walking too: Monaco's harbour ferry (route=ferry) is kept in the file."""
    assert routers["before"].matrix(points, costing) == routers["osm"].matrix(points, costing)


def test_the_ferry_is_in_both_files(routers):
    ferries = {way: value for way, value in routers["osm"].ways.items()
               if value[1].get("route") == "ferry" and "highway" not in value[1]}
    assert ferries
    for which in ("before", "after"):
        assert {way: routers[which].ways.get(way) for way in ferries} == ferries


@pytest.mark.parametrize("costing", ["auto", "bus", "truck", "bicycle"])
def test_footbridge_changes_nothing_for_vehicles(routers, points, costing):
    before = routers["before"].matrix(points, costing, shortest=True)
    after = routers["after"].matrix(points, costing, shortest=True)
    assert differing(before, after, tolerance=0.001) == 0


def test_footbridge_shortens_the_walk_across_the_harbour(routers):
    start, end = FOOTBRIDGE.coords[0], FOOTBRIDGE.coords[-1]
    before = routers["before"].route(start, end, "pedestrian")
    after = routers["after"].route(start, end, "pedestrian")
    (bridge,) = routers["after"].custom_way_ids()

    assert after.way_ids == (bridge,)
    assert after.length_m < before.length_m - 200

    driving = routers["after"].route(start, end, "auto")
    assert driving is None or not driving.uses(bridge)


# ---------------------------------------------------------------------
# Changing an existing street, the way a QGIS user would
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def closed_street(extract, tmp_path_factory):
    """
    Build once, open the before GeoPackage, copy the rows of one named
    two-way street, set access=no, and build again with those rows as
    the custom layer. Returns (before router, after router, way id).
    """
    folder = tmp_path_factory.mktemp("closure")
    header = osmium.io.Reader(str(extract), osmium.osm.osm_entity_bits.NOTHING).header().box()
    area = gpd.GeoDataFrame(geometry=[box(
        header.bottom_left.lon - 0.01, header.bottom_left.lat - 0.01,
        header.top_right.lon + 0.01, header.top_right.lat + 0.01)], crs="EPSG:4326")
    bridge = gpd.GeoDataFrame({"highway": ["footway"], "bridge": ["yes"], "layer": ["1"]},
                              geometry=[FOOTBRIDGE], crs="EPSG:4326")

    _, _, osm_nodes, osm_edges = build_network(area, bridge, osm_source=extract,
                                               return_source_osm=True)
    write_gpkg(osm_nodes, osm_edges, folder / "before.gpkg")
    layer = gpd.read_file(folder / "before.gpkg", layer="edges")

    streets = layer[layer.highway.isin(["residential", "tertiary", "secondary"])
                    & (layer.car_direction == "both") & layer.name.notna()]
    way = int(streets.osmid.value_counts().index[0])  # the street with the most stretches
    rows = layer[layer.osmid == way].copy()
    rows["access"] = "no"

    nodes, edges, osm_nodes, osm_edges = build_network(
        area, rows, osm_source=extract, return_source_osm=True)
    write_osm(osm_nodes, osm_edges, folder / "before.osm.pbf")
    write_osm(nodes, edges, folder / "after.osm.pbf")
    return (Router.from_pbf(folder / "before.osm.pbf", folder / "before"),
            Router.from_pbf(folder / "after.osm.pbf", folder / "after"), way)


def test_closed_street_is_no_longer_driven_along(closed_street):
    before, after, way = closed_street
    refs, tags = before.ways[way]
    start, end = before.node(refs[0]), before.node(refs[-1])

    changed = [w for w, (_, t) in after.ways.items() if t.get("nf:modified") == "yes"]
    assert changed and all(after.ways[w][1]["access"] == "no" for w in changed)
    assert all(after.ways[w][1]["name"] == tags["name"] for w in changed)

    assert before.route(start, end, "auto", shortest=True).uses(way)
    detour = after.route(start, end, "auto", shortest=True)
    assert detour is None or not (set(detour.way_ids) & {way, *changed})


def test_closing_one_street_leaves_the_rest_of_the_file_alone(closed_street):
    before, after, way = closed_street
    changed = {w for w, (_, t) in after.ways.items() if t.get("nf:modified") == "yes"}

    assert {w: v for w, v in after.ways.items() if w not in changed and w != way} == {
        w: v for w, v in before.ways.items() if w != way}


# ---------------------------------------------------------------------
# A standalone network: real streets given as plain lines
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def streets_as_lines(extract, tmp_path_factory):
    """
    Monaco's streets as a user might hold them: one line per street with
    its tags as attributes, and no OpenStreetMap behind it. Built
    standalone (join_at="vertices": the data has a vertex at every
    junction, and its many flyovers must not become junctions).

    Returns (Valhalla on that network, Valhalla on the extract reduced
    to what lines can carry: the same streets and tags, without
    relations, node tags or ferries).
    """
    folder = tmp_path_factory.mktemp("lines")
    location = {node.id: (node.location.lon, node.location.lat)
                for node in osmium.FileProcessor(str(extract), osmium.osm.NODE)}
    rows, geometries = [], []
    for way in osmium.FileProcessor(str(extract), osmium.osm.WAY):
        tags = dict(way.tags)
        if tags.get("highway") in KNOWN_HIGHWAYS and tags.get("area") != "yes":
            rows.append({"osm_id": way.id,
                         **{k: v for k, v in tags.items() if k in KNOWN_TAG_KEYS}})
            geometries.append(LineString([location[n.ref] for n in way.nodes]))
    lines = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")

    nodes, edges = build_network(None, lines, standalone=True, join_at="vertices")
    write_osm(nodes, edges, folder / "lines.osm.pbf")

    writer = osmium.SimpleWriter(str(folder / "reduced.osm.pbf"))
    kept = set(lines["osm_id"])
    for obj in osmium.FileProcessor(str(extract)):
        if obj.is_node():
            writer.add_node(osmium.osm.mutable.Node(id=obj.id, location=obj.location))
        elif obj.is_way() and obj.id in kept:
            writer.add_way(osmium.osm.mutable.Way(
                id=obj.id, nodes=[n.ref for n in obj.nodes],
                tags={k: v for k, v in dict(obj.tags).items() if k in KNOWN_TAG_KEYS}))
    writer.close()

    return (Router.from_pbf(folder / "lines.osm.pbf", folder / "lines"),
            Router.from_pbf(folder / "reduced.osm.pbf", folder / "reduced"))


@pytest.mark.parametrize("costing", ["auto", "bicycle", "pedestrian"])
def test_streets_given_as_lines_route_like_the_same_streets_in_osm(streets_as_lines, points,
                                                                   costing):
    from_lines, reduced = streets_as_lines
    built = from_lines.matrix(points, costing, shortest=True)
    expected = reduced.matrix(points, costing, shortest=True)

    # Valhalla keeps each edge's length in whole metres, and the two files
    # cut the streets into edges at the same junctions but number them
    # differently: a trip can come out a metre or two apart.
    assert differing(built, expected, tolerance=0.005, metres=3) == 0


# ---------------------------------------------------------------------
# A turn restriction drawn at a real junction
# ---------------------------------------------------------------------

def test_turn_restriction_at_a_real_junction(extract, routers, tmp_path_factory):
    """
    Find a junction of two-way streets, draw a turn line through it
    (20 m in on one street, 20 m out on another), ban that turn, and
    check Valhalla drives round instead.
    """
    from shapely.geometry import Point

    from networkforge.projection import get_analysis_crs
    from networkforge.turns import _turn_direction

    before = routers["before"]
    streets = {way: (refs, tags) for way, (refs, tags) in before.ways.items()
               if tags.get("highway") in ("residential", "tertiary", "secondary")
               and "oneway" not in tags and len(refs) > 3}
    on = {}
    for way, (refs, _) in streets.items():
        for position, node in enumerate(refs):
            on.setdefault(node, []).append((way, position))

    def turns_at(node):
        """(street in, its position of node; street out, its position of node)."""
        for way_a, at_a in on[node]:
            for way_b, at_b in on[node]:
                if way_a != way_b and at_a >= 1 and at_b <= len(streets[way_b][0]) - 2:
                    yield way_a, at_a, way_b, at_b

    # How many ways each node is on: a turn line must pass no other junction.
    ways_at = {}
    for refs, _ in before.ways.values():
        for ref in set(refs):
            ways_at[ref] = ways_at.get(ref, 0) + 1

    def stretch(refs, start, step):
        """Nodes from refs[start] along the way (step -1 or +1) for 15 m, or None."""
        nodes, length = [refs[start]], 0.0
        while length < 15:
            index = start + step * len(nodes)
            if not 0 <= index < len(refs) or ways_at.get(refs[index], 0) > 1:
                return None
            nodes.append(refs[index])
            a, b = gpd.GeoSeries([Point(before.node(n)) for n in nodes[-2:]],
                                 crs=4326).to_crs(crs)
            length += a.distance(b)
        return nodes

    def candidates():
        """Real turns: in on one street, out on another, the shortest drive going that way."""
        for node in sorted(on):
            for way_a, at_a, way_b, at_b in turns_at(node):
                back = stretch(streets[way_a][0], at_a, -1)
                ahead = stretch(streets[way_b][0], at_b, +1)
                if back is None or ahead is None:
                    continue
                ends = [*back[::-1], *ahead[1:]]
                points = gpd.GeoSeries([Point(before.node(n)) for n in ends],
                                       crs=4326).to_crs(crs)
                came_from, via, goes_to = (points.iloc[len(back) - 2], points.iloc[len(back) - 1],
                                           points.iloc[len(back)])
                if _turn_direction(came_from, via, via, goes_to) not in ("left", "right"):
                    continue
                direct = before.route(before.node(ends[0]), before.node(ends[-1]), "auto",
                                      shortest=True)
                if direct is not None and direct.way_ids == (way_a, way_b):
                    yield LineString(points.tolist()), (came_from, via, goes_to), ends, direct

    crs = get_analysis_crs(gpd.GeoDataFrame(geometry=[Point(before.node(next(iter(on))))],
                                            crs=4326))
    line, (came_from, via, goes_to), ends, direct = next(candidates())
    turn = _turn_direction(came_from, via, via, goes_to)

    folder = tmp_path_factory.mktemp("turn")
    header = osmium.io.Reader(str(extract), osmium.osm.osm_entity_bits.NOTHING).header().box()
    area = gpd.GeoDataFrame(geometry=[box(
        header.bottom_left.lon - 0.01, header.bottom_left.lat - 0.01,
        header.top_right.lon + 0.01, header.top_right.lat + 0.01)], crs="EPSG:4326")
    feature = gpd.GeoDataFrame({"restriction": [f"no_{turn}_turn"]}, geometry=[line], crs=crs)
    nodes, edges = build_network(area, feature, osm_source=extract)
    write_osm(nodes, edges, folder / "after.osm.pbf")
    after = Router.from_pbf(folder / "after.osm.pbf", folder / "tiles")

    start, end = before.node(ends[0]), before.node(ends[-1])
    detour = after.route(start, end, "auto", shortest=True)
    walking = after.route(start, end, "pedestrian", shortest=True)

    assert detour is None or detour.length_m > direct.length_m + 5
    assert walking.length_m == pytest.approx(direct.length_m, abs=5)
