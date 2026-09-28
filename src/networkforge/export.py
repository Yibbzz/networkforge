"""
Write a NetworkForge network as OpenStreetMap data.

write_osm() picks the format from the file name:

    network.osm                 OSM XML
    network.osm.pbf (or .pbf)   OSM PBF: compressed binary, what routers
                                such as GraphHopper and Valhalla prefer
    network.osm.gz / .osm.bz2   compressed OSM XML

XML is written by this module; the other formats by pyosmium. Both get
the same content from prepare_osm_data(), so an XML and a PBF export of
one network hold identical nodes, ways and tags. Nodes and ways are
sorted by id, as tools like osmium expect.

Each graph edge becomes its own 2-node way with a new id (one OSM way
can produce many edges). Way tags are the ones listed in tags.py plus
every access tag the mode rules use; node tags (barriers, signals,
crossings) are NODE_TAGS.
"""

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd

from .errors import InputError
from .modes import mode_tag_keys
from .tags import BASE_WAY_TAGS, CUSTOM_COLUMN, CUSTOM_TAG, NODE_TAGS, ROUTING_WAY_TAGS

GENERATOR = "NetworkForge"

XML_SUFFIXES = (".osm",)
OSMIUM_SUFFIXES = (".osm.pbf", ".pbf", ".osm.gz", ".osm.bz2")


@dataclass(frozen=True)
class OSMData:
    """A network ready to write: WGS84 coordinates, string tags, sorted by id."""

    bounds: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat
    nodes: list[tuple[int, float, float, dict[str, str]]]  # id, lon, lat, tags
    ways: list[tuple[int, list[int], dict[str, str]]]  # id, node refs, tags


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

    nodes = nodes_gdf.to_crs("EPSG:4326")
    edges = edges_gdf.to_crs("EPSG:4326")

    node_bounds, edge_bounds = nodes.total_bounds, edges.total_bounds
    bounds = (
        round(min(node_bounds[0], edge_bounds[0]), 7),
        round(min(node_bounds[1], edge_bounds[1]), 7),
        round(max(node_bounds[2], edge_bounds[2]), 7),
        round(max(node_bounds[3], edge_bounds[3]), 7),
    )

    node_tags = _row_tags(nodes, {key: key for key in NODE_TAGS})
    osm_nodes = sorted(zip(
        nodes.index.astype("int64").tolist(),
        [round(x, 7) for x in nodes.geometry.x.tolist()],
        [round(y, 7) for y in nodes.geometry.y.tolist()],
        node_tags,
        strict=True,
    ))

    way_tags = _row_tags(edges, way_tag_columns())
    refs = zip(edges["u"].astype("int64").tolist(), edges["v"].astype("int64").tolist(),
               strict=True)
    osm_ways = [
        (way_id, [u, v], tags)
        for way_id, ((u, v), tags) in enumerate(zip(refs, way_tags, strict=True), start=1)
    ]

    return OSMData(bounds, osm_nodes, osm_ways)


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
    finally:
        writer.close()
