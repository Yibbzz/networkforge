"""
Write a NetworkForge network out.

Two kinds of output, for two audiences:

- write_osm(): OpenStreetMap data (.osm, .osm.pbf, ...) for routing
  engines such as Valhalla and GraphHopper, which read the tags
  themselves.
- write_gpkg(): a GeoPackage for QGIS, with ready-made analysis columns
  (which modes may use each edge, speed, travel times, direction) for
  QGIS's network analysis tools. See analysis_edges().

write_osm() picks the format from the file name:

    network.osm                 OSM XML
    network.osm.pbf (or .pbf)   OSM PBF: compressed binary, what routers
                                such as GraphHopper and Valhalla prefer
    network.osm.gz / .osm.bz2   compressed OSM XML

XML is written by this module; the other formats by pyosmium. Both get
the same content from prepare_osm_data(), so an XML and a PBF export of
one network hold identical nodes, ways, relations and tags. Each kind
is sorted by id, as tools like osmium expect.

The existing network is written as OpenStreetMap has it (see
osm.OSMSource): each way keeps its id, its nodes in order and every
tag, tagged nodes keep theirs, and turn restrictions and route
relations are kept. The build only adds to it: where a custom line
joins an existing way mid-way, the new junction node is inserted into
that way. This matters to routers - Valhalla reads far more tags than
the edge table holds, obeys turn restrictions (which name way ids), and
measures curvature and junction density from whole ways.

Each custom line is one way with a new id (above every OSM way id),
its nodes being the line's vertices and junctions, tagged with the
attributes listed in tags.py plus `nf:custom=yes`.

Edges without a source (tables not made by build_network, e.g. straight
from OSMnx) are still written: ways are rebuilt from the `osmid` column
and tags come from the columns.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
import shapely

from .errors import InputError
from .modes import allows_mode, car_speed_kph, mode_tag_keys
from .osm import SOURCE_ATTR, OSMSource
from .tags import (
    BASE_WAY_TAGS,
    CUSTOM_COLUMN,
    CUSTOM_TAG,
    NODE_TAGS,
    PART_COLUMN,
    ROUTING_WAY_TAGS,
)

GENERATOR = "NetworkForge"

# Constant speeds for bike and walk travel times in the GeoPackage.
BIKE_KPH = 15.0
WALK_KPH = 5.0

# Columns analysis_edges() adds for QGIS network analysis.
GPKG_ANALYSIS_COLUMNS = (
    "car", "bike", "walk", "speed_kph", "length_m",
    "car_minutes", "bike_minutes", "walk_minutes",
    "car_direction", "bike_direction",
)

# Edge columns that only mean something inside the pipeline.
INTERNAL_COLUMNS = ["key", "split", "reversed", "length", PART_COLUMN]

MEMBER_TYPES = {"n": "node", "w": "way", "r": "relation"}

XML_SUFFIXES = (".osm",)
OSMIUM_SUFFIXES = (".osm.pbf", ".pbf", ".osm.gz", ".osm.bz2")


@dataclass(frozen=True)
class OSMData:
    """A network ready to write: WGS84 coordinates, string tags, sorted by id."""

    bounds: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat
    nodes: list[tuple[int, float, float, dict[str, str]]]  # id, lon, lat, tags
    ways: list[tuple[int, list[int], dict[str, str]]]  # id, node refs, tags
    # id, [(member type "n"/"w"/"r", ref, role)], tags
    relations: list[tuple[int, list[tuple[str, int, str]], dict[str, str]]] = ()


def way_tag_columns() -> dict[str, str]:
    """Edge column -> OSM tag key, for every way tag export writes."""
    columns = {key: key for key in BASE_WAY_TAGS}
    for key in [*sorted(mode_tag_keys()), *ROUTING_WAY_TAGS]:
        columns.setdefault(key, key)
    columns[CUSTOM_COLUMN] = CUSTOM_TAG
    return columns


def write_osm(
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    output_file_path: str | Path,
) -> None:
    """
    Write the network to `output_file_path`, in the format its name
    implies (.osm, .osm.pbf, .pbf, .osm.gz, .osm.bz2). An existing file
    is replaced.
    """
    name = str(output_file_path).lower()

    if name.endswith(XML_SUFFIXES):
        _write_xml(prepare_osm_data(nodes_gdf, edges_gdf), output_file_path)
    elif name.endswith(OSMIUM_SUFFIXES):
        _write_with_osmium(prepare_osm_data(nodes_gdf, edges_gdf), output_file_path)
    else:
        raise InputError(
            f"Can't tell the output format from {Path(output_file_path).name!r}. "
            f"Use one of: {', '.join(XML_SUFFIXES + OSMIUM_SUFFIXES)}"
        )


def write_gpkg(
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    output_file_path: str | Path,
) -> None:
    """
    Write a GeoPackage for QGIS with a 'nodes' and an 'edges' layer.
    Edges carry the analysis columns from analysis_edges(). An existing
    file is replaced.
    """
    path = Path(output_file_path)
    path.unlink(missing_ok=True)

    layers = {
        "nodes": nodes_gdf.reset_index(),
        "edges": analysis_edges(nodes_gdf, edges_gdf),
    }
    for name, gdf in layers.items():
        gdf = gdf.copy()
        for column in gdf.columns.drop(gdf.geometry.name):
            if gdf[column].dtype == object:  # OSMnx can merge values into lists
                gdf[column] = gdf[column].map(
                    lambda v: ";".join(map(str, v)) if isinstance(v, list) else v)
        gdf.to_file(path, layer=name, driver="GPKG")


def analysis_edges(nodes_gdf: gpd.GeoDataFrame, edges_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Edges ready for QGIS network analysis (Service area, Shortest path,
    QNEAT3). Compared with the pipeline's edges:

    - one row per street: OSMnx's reverse copy of each two-way street
      is dropped (the direction columns say which way you can travel);
    - each geometry runs exactly from its u node to its v node, so QGIS
      connects edges at shared nodes;
    - added columns:
        car, bike, walk        may this mode use the edge (same rules as routing)
        speed_kph              car speed: maxspeed, else a default per road type
        length_m               edge length in metres, measured in the (local, metric)
                               analysis CRS - up to ~0.5% longer than OSMnx's
                               spherical great-circle lengths, and more accurate
        car_minutes, bike_minutes, walk_minutes
                               travel times (bike 15 km/h, walk 5 km/h)
        car_direction, bike_direction
                               forward / backward / both, relative to the line's
                               direction (walking is always both). Bikes follow
                               oneway:bicycle=no / cycleway=opposite* (contraflow).
    """
    edges = edges_gdf.copy()

    oneway = edges["oneway"] if "oneway" in edges else pd.Series(None, index=edges.index)
    car_direction = oneway.map(_direction)
    if "reversed" in edges:
        reverse_copy = (edges["reversed"] == True) & (car_direction == "both")  # noqa: E712
        edges, car_direction = edges[~reverse_copy], car_direction[~reverse_copy]

    nodes = nodes_gdf.geometry
    edges = edges.set_geometry(
        shapely.linestrings(np.stack([
            shapely.get_coordinates(nodes.loc[edges["u"].astype("int64")].to_numpy()),
            shapely.get_coordinates(nodes.loc[edges["v"].astype("int64")].to_numpy()),
        ], axis=1)),
        crs=nodes_gdf.crs,
    )

    # Access and speed depend only on tags; evaluate each distinct
    # combination once (a few thousand), not each row.
    keys = [k for k in sorted(mode_tag_keys() | {"highway", "maxspeed"}) if k in edges.columns]
    combos = list(zip(*(edges[k].map(_hashable) for k in keys), strict=True)) if keys else [()]
    results = {}
    for combo in set(combos):
        tags = {k: v for k, v in zip(keys, combo, strict=True) if v is not None}
        results[combo] = (allows_mode(tags, "drive"), allows_mode(tags, "bike"),
                          allows_mode(tags, "walk"), car_speed_kph(tags))
    car, bike, walk, speed = (np.array(v) for v in zip(*(results[c] for c in combos), strict=True))

    length = edges.geometry.length.to_numpy()
    bike_direction = car_direction.copy()
    if "oneway:bicycle" in edges:
        bike_direction[edges["oneway:bicycle"].astype(str).eq("no")] = "both"
    if "cycleway" in edges:  # the older way to tag contraflow cycling
        bike_direction[edges["cycleway"].astype(str).str.startswith("opposite")] = "both"

    analysis = pd.DataFrame({
        "car": car.astype(bool),
        "bike": bike.astype(bool),
        "walk": walk.astype(bool),
        "speed_kph": speed.round(1),
        "length_m": length.round(2),
        "car_minutes": (length / 1000 / speed * 60).round(4),
        "bike_minutes": (length / 1000 / BIKE_KPH * 60).round(4),
        "walk_minutes": (length / 1000 / WALK_KPH * 60).round(4),
        "car_direction": car_direction.to_numpy(),
        "bike_direction": bike_direction.to_numpy(),
    }, index=edges.index)

    edges = edges.drop(columns=[c for c in INTERNAL_COLUMNS if c in edges.columns])
    return pd.concat([edges, analysis], axis=1).reset_index(drop=True)


def _direction(oneway) -> str:
    """QGIS direction value for an oneway tag, relative to the edge's direction."""
    text = str(oneway).lower() if oneway is not None and oneway == oneway else ""
    if text in ("true", "yes", "1"):
        return "forward"
    if text in ("-1", "reverse"):
        return "backward"
    return "both"


def _hashable(value):
    if isinstance(value, list):
        return ";".join(map(str, value))
    if value is None or (isinstance(value, float) and value != value):
        return None
    return value


def write_osm_xml(
    combined_points_gdf: gpd.GeoDataFrame,
    split_lines_combined_gdf: gpd.GeoDataFrame,
    output_file_path: str | Path,
) -> None:
    """Write the network as OSM XML, whatever the file name (see write_osm)."""
    _write_xml(prepare_osm_data(combined_points_gdf, split_lines_combined_gdf), output_file_path)


def prepare_osm_data(nodes_gdf: gpd.GeoDataFrame, edges_gdf: gpd.GeoDataFrame) -> OSMData:
    """
    Convert network GeoDataFrames (any CRS) to OSMData. Edges must have
    `u` and `v` columns referencing the node index.
    """

    for label, gdf in (("Node", nodes_gdf), ("Edge", edges_gdf)):
        if gdf.crs is None:
            raise InputError(f"{label} GeoDataFrame must have a CRS assigned.")
    for column in ("u", "v"):
        if column not in edges_gdf.columns:
            raise InputError(f"Edge GeoDataFrame is missing required column: {column}")

    source = edges_gdf.attrs.get(SOURCE_ATTR)
    nodes = nodes_gdf.to_crs("EPSG:4326")

    # Every edge runs between nodes, so the nodes give the extent.
    min_lon, min_lat, max_lon, max_lat = nodes.total_bounds
    bounds = (round(min_lon, 7), round(min_lat, 7), round(max_lon, 7), round(max_lat, 7))

    node_ids = nodes.index.astype("int64").tolist()
    if source is None:
        node_tags = _row_tags(nodes, {key: key for key in NODE_TAGS})
    else:
        node_tags = [source.node_tags.get(node_id, {}) for node_id in node_ids]
    osm_nodes = sorted(zip(
        node_ids,
        [round(x, 7) for x in nodes.geometry.x.tolist()],
        [round(y, 7) for y in nodes.geometry.y.tolist()],
        node_tags,
        strict=True,
    ))

    is_custom = (edges_gdf[CUSTOM_COLUMN] == "yes" if CUSTOM_COLUMN in edges_gdf
                 else pd.Series(False, index=edges_gdf.index))
    existing = edges_gdf[~is_custom]
    if "reversed" in existing:
        # OSMnx holds a two-way street as two edges; one way covers both.
        existing = existing[existing["reversed"] != True]  # noqa: E712

    # (way id or None for "needs a new one", node refs, tags)
    ways = _existing_ways(existing, source) + _custom_ways(edges_gdf[is_custom])

    new_ids = iter(range(max((way_id for way_id, _, _ in ways if way_id), default=0) + 1,
                         2**62))
    osm_ways = sorted((way_id or next(new_ids), refs, tags) for way_id, refs, tags in ways)

    relations = _relations(source, osm_ways, set(node_ids)) if source is not None else []
    return OSMData(bounds, osm_nodes, osm_ways, relations)


def _existing_ways(edges: pd.DataFrame, source: OSMSource | None) -> list[tuple]:
    """
    The existing network's edges (one per street segment and direction
    of drawing) as ways. With a source, each way is its OSM original:
    untouched ways are copied; ways the build cut (a new junction) or
    cropped (the edge of the area) are rebuilt around their OSM nodes.
    """
    if edges.empty:
        return []

    us = edges["u"].astype("int64").to_numpy()
    vs = edges["v"].astype("int64").to_numpy()
    if "osmid" in edges:
        # A real way id is one whole number; anything else (missing, or a
        # list from a simplified OSMnx graph) makes the edge its own way.
        way_ids = pd.to_numeric(edges["osmid"], errors="coerce").fillna(0).astype("int64")
        way_ids = way_ids.to_numpy()
    else:
        way_ids = np.zeros(len(edges), dtype="int64")

    known = np.zeros(len(edges), dtype=bool)
    ways = []

    if source is not None:
        known = np.fromiter((way_id in source.ways for way_id in way_ids.tolist()),
                            dtype=bool, count=len(edges))
        cut = (edges["split"] == "yes").to_numpy() if "split" in edges else np.zeros_like(known)
        counts = pd.Series(way_ids[known]).value_counts()
        cut_ids = set(way_ids[known & cut].tolist())
        segments = defaultdict(list)

        rebuild = []
        for way_id, count in counts.items():
            refs, tags = source.ways[way_id]
            if way_id in cut_ids or count != len(refs) - 1:
                rebuild.append(way_id)
            else:
                ways.append((way_id if way_id > 0 else None, list(refs), dict(tags)))

        rows = known & np.isin(way_ids, rebuild)
        for way_id, u, v in zip(way_ids[rows].tolist(), us[rows].tolist(), vs[rows].tolist(),
                                strict=True):
            segments[way_id].append((u, v))
        for way_id in rebuild:
            refs, tags = source.ways[way_id]
            for number, run in enumerate(_runs(refs, segments[way_id])):
                keeps_id = number == 0 and way_id > 0
                ways.append((way_id if keeps_id else None, run, dict(tags)))

    # No source for these: chain each way's edges, tags from the columns.
    rest = edges[~known]
    if not rest.empty:
        tags = _row_tags(rest, way_tag_columns())
        groups = defaultdict(list)
        for row, way_id in enumerate(way_ids[~known].tolist()):
            groups[way_id or ("edge", row)].append(row)
        rest_us, rest_vs = us[~known].tolist(), vs[~known].tolist()
        for way_id, rows in groups.items():
            chains = _chains([(rest_us[row], rest_vs[row]) for row in rows])
            for number, chain in enumerate(chains):
                keeps_id = number == 0 and isinstance(way_id, int) and way_id > 0
                ways.append((way_id if keeps_id else None, chain, tags[rows[0]]))

    return ways


def _custom_ways(edges: pd.DataFrame) -> list[tuple]:
    """Custom edges as ways: one per custom line (or per edge if unnumbered)."""
    if edges.empty:
        return []

    tags = _row_tags(edges, way_tag_columns())
    us = edges["u"].astype("int64").tolist()
    vs = edges["v"].astype("int64").tolist()
    parts = edges[PART_COLUMN].tolist() if PART_COLUMN in edges else [None] * len(edges)

    groups = defaultdict(list)
    for row, part in enumerate(parts):
        numbered = part is not None and part == part  # not NaN
        groups[("part", part) if numbered else ("edge", row)].append(row)

    return [
        (None, chain, tags[rows[0]])
        for rows in groups.values()
        for chain in _chains([(us[row], vs[row]) for row in rows])
    ]


def _chains(segments: list[tuple[int, int]]) -> list[list[int]]:
    """
    Join directed segments (u, v) end to start into as few node chains
    as it takes to use each segment once. A line cut into pieces gives
    back its nodes in order; a closed loop starts at its first segment.
    """
    following = defaultdict(list)
    arrivals = defaultdict(int)
    for u, v in segments:
        following[u].append(v)
        arrivals[v] += 1

    def walk(node: int) -> list[int]:
        chain = [node]
        while following[node]:
            node = following[node].pop(0)
            chain.append(node)
        return chain

    chains = []
    # Start where a chain must start (more segments leave than arrive),
    # then pick up whatever is left (loops).
    for start in [node for node, _ in segments if len(following[node]) > arrivals[node]]:
        while len(following[start]) > arrivals[start]:
            chains.append(walk(start))
    for start, _ in segments:
        while following[start]:
            chains.append(walk(start))
    return chains


def _runs(refs: list[int], segments: list[tuple[int, int]]) -> list[list[int]]:
    """
    An OSM way's nodes after the build, from its original `refs` and
    the (u, v) of its remaining edges: nodes the build inserted between
    two neighbours are added in place; where a stretch is gone (cropped
    at the edge of the area) the way breaks into separate runs.
    """
    joined = defaultdict(set)
    for u, v in segments:
        joined[u].add(v)
        joined[v].add(u)
    original = set(refs)

    def via_new_nodes(start: int, end: int) -> list[int] | None:
        """Nodes after `start` up to `end`, passing only inserted nodes."""
        stack = [(node, [node]) for node in sorted(joined[start] - original)]
        seen = {start}
        while stack:
            node, path = stack.pop()
            if end in joined[node]:
                return [*path, end]
            seen.add(node)
            stack.extend((nxt, [*path, nxt]) for nxt in sorted(joined[node] - original - seen))
        return None

    runs, run = [], [refs[0]]
    for start, end in zip(refs, refs[1:], strict=False):
        if start == end:
            continue
        path = [end] if end in joined[start] else via_new_nodes(start, end)
        if path is None:
            runs.append(run)
            run = [end]
        else:
            run.extend(path)
    runs.append(run)
    return [run for run in runs if len(run) >= 2]


def _relations(source: OSMSource, ways: list[tuple], node_ids: set[int]) -> list[tuple]:
    """
    The source's relations that still make sense in the written network.
    A turn restriction needs every member, with its via node on the ways
    it joins; a route keeps whichever of its members are there.
    """
    refs_of = {way_id: refs for way_id, refs, _ in ways}
    relations = []

    for relation_id, tags, members in source.relations:
        present = [
            (kind, ref, role) for kind, ref, role in members
            if (kind == "w" and ref in refs_of) or (kind == "n" and ref in node_ids)
        ]
        if tags.get("type") == "route":
            if any(kind == "w" for kind, _, _ in present):
                relations.append((relation_id, present, dict(tags)))
            continue

        via_nodes = [ref for kind, ref, role in members if kind == "n" and role == "via"]
        member_ways = [refs_of[ref] for kind, ref, _ in present if kind == "w"]
        if len(present) == len(members) and all(
            node in refs for node in via_nodes for refs in member_ways
        ):
            relations.append((relation_id, present, dict(tags)))

    return sorted(relations)


def _row_tags(gdf: gpd.GeoDataFrame, columns: dict[str, str]) -> list[dict[str, str]]:
    """
    One {osm_key: text} dict per row, for the given column -> key map.
    Goes column by column and only visits cells that have a value: most
    tag columns are almost entirely empty.
    """
    tags = [{} for _ in range(len(gdf))]
    for column, key in columns.items():
        if column not in gdf.columns:
            continue
        values = gdf[column]
        present = np.flatnonzero(values.notna().to_numpy())
        for row, value in zip(present.tolist(), values.iloc[present].tolist(), strict=True):
            text = _tag_text(value)
            if text is not None:
                tags[row][key] = text
    return tags


def _tag_text(value) -> str | None:
    """A tag value as OSM text, or None if missing."""
    if isinstance(value, list):  # OSMnx can merge values into lists
        value = ";".join(map(str, value))
    if isinstance(value, bool | np.bool_):
        # OSMnx turns oneway into True/False. OSM (and OSMnx when reading
        # it back) only understands yes/no: "True" made one-way streets
        # two-way. With simplify=False each edge already runs in the
        # allowed direction, so True is plain oneway=yes.
        return "yes" if value else "no"
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # 2.0 -> "2"
    return str(value)


def _write_xml(data: OSMData, path: str | Path) -> None:
    """Stream OSM XML straight to the file (no in-memory element tree)."""

    def tag_lines(tags: dict[str, str]) -> str:
        return "".join(f"    <tag k={quoteattr(k)} v={quoteattr(v)}/>\n" for k, v in tags.items())

    min_lon, min_lat, max_lon, max_lat = data.bounds
    with open(path, "w", encoding="utf-8") as out:
        out.write("<?xml version='1.0' encoding='utf-8'?>\n")
        out.write(f'<osm version="0.6" generator="{GENERATOR}">\n')
        out.write(f'  <bounds minlat="{min_lat}" minlon="{min_lon}" '
                  f'maxlat="{max_lat}" maxlon="{max_lon}"/>\n')

        for node_id, lon, lat, tags in data.nodes:
            if tags:
                out.write(f'  <node id="{node_id}" lat="{lat}" lon="{lon}">\n'
                          f"{tag_lines(tags)}  </node>\n")
            else:
                out.write(f'  <node id="{node_id}" lat="{lat}" lon="{lon}"/>\n')

        for way_id, refs, tags in data.ways:
            nds = "".join(f'    <nd ref="{ref}"/>\n' for ref in refs)
            out.write(f'  <way id="{way_id}">\n{nds}{tag_lines(tags)}  </way>\n')

        for relation_id, members, tags in data.relations:
            lines = "".join(
                f'    <member type="{MEMBER_TYPES[kind]}" ref="{ref}" role={quoteattr(role)}/>\n'
                for kind, ref, role in members
            )
            out.write(f'  <relation id="{relation_id}">\n{lines}{tag_lines(tags)}'
                      "  </relation>\n")

        out.write("</osm>\n")


def _write_with_osmium(data: OSMData, path: str | Path) -> None:
    header = osmium.io.Header()
    header.set("generator", GENERATOR)
    min_lon, min_lat, max_lon, max_lat = data.bounds
    header.add_box(osmium.osm.Box(osmium.osm.Location(min_lon, min_lat),
                                  osmium.osm.Location(max_lon, max_lat)))

    writer = osmium.SimpleWriter(str(path), header=header, overwrite=True)
    try:
        for node_id, lon, lat, tags in data.nodes:
            writer.add_node(osmium.osm.mutable.Node(id=node_id, location=(lon, lat), tags=tags))
        for way_id, refs, tags in data.ways:
            writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=refs, tags=tags))
        for relation_id, members, tags in data.relations:
            writer.add_relation(osmium.osm.mutable.Relation(
                id=relation_id, members=members, tags=tags))
    finally:
        writer.close()
