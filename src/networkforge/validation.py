"""
Checks on custom data and on the finished network.

Before the build: resolve_custom_tags / check_custom_tags (feature tags
vs OSM rules and the chosen network type).

After the build, structural invariants the network must satisfy:

    assert_all_edges_have_valid_nodes
    assert_no_u_equals_v
    assert_all_custom_edges_are_connected
    assert_custom_lines_unbroken

Each raises NetworkIntegrityError with a message identifying the
offending rows/nodes, and returns None if everything is fine. They run
at the end of build_network and are also used directly by the tests
(see tests/live/test_structural_invariants.py). Connectivity uses a
small in-module union-find.

build_network doesn't run assert_all_custom_edges_are_connected: custom
lines that don't reach the network are usually the user's data, not a
broken build, so it only warns about them (disconnected_custom_edges).
The tests still assert it wherever the lines are meant to connect.
"""

import collections
import difflib
import logging
import re

import geopandas as gpd
import pandas as pd

from .errors import InvalidTagsError, NetworkIntegrityError
from .modes import allows_mode, mode_tag_keys, usable_modes
from .tags import BASE_WAY_TAGS, ROUTING_WAY_TAGS

log = logging.getLogger(__name__)


def assert_all_edges_have_valid_nodes(
    edges_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
) -> None:
    """
    Every edge's 'u' and 'v' must reference a node id that actually
    exists in points_gdf's index. A dangling reference here means
    write_osm_xml will emit a <nd ref="..."> with no matching <node>,
    which is invalid OSM XML and can cause the edge (or the whole way)
    to be silently dropped when reloaded by a routing engine.
    """
    node_ids = set(points_gdf.index)

    bad = edges_gdf[
        ~(edges_gdf["u"].isin(node_ids) & edges_gdf["v"].isin(node_ids))
    ]

    if not bad.empty:
        missing = (set(bad["u"]) | set(bad["v"])) - node_ids
        raise NetworkIntegrityError(
            f"{len(bad)} edge(s) reference node id(s) not present in the "
            f"node table: {sorted(missing, key=str)}"
        )


def assert_no_u_equals_v(edges_gdf: gpd.GeoDataFrame) -> None:
    """
    No edge should be a self-loop (u == v). These are typically an
    artifact of splitting a line exactly at its own endpoint and
    should always have been filtered out before export.
    """
    bad = edges_gdf[edges_gdf["u"] == edges_gdf["v"]]

    if not bad.empty:
        raise NetworkIntegrityError(
            f"{len(bad)} edge(s) have u == v (self-loop) at index(es): "
            f"{list(bad.index)}"
        )


def _stranded_custom_edges(edges_gdf: gpd.GeoDataFrame) -> tuple[pd.Series, int]:
    """
    (mask of custom edges outside the largest connected component,
    number of components).
    """
    nothing = pd.Series(False, index=edges_gdf.index)

    if "custom" not in edges_gdf.columns:
        return nothing, 0

    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for u, v in zip(edges_gdf["u"], edges_gdf["v"], strict=True):
        union(u, v)

    sizes = collections.Counter(find(node) for node in parent)

    if len(sizes) <= 1:
        return nothing, len(sizes)

    largest_root = max(sizes, key=sizes.get)

    custom = edges_gdf["custom"] == "yes"
    stranded = edges_gdf.loc[custom, "u"].map(find) != largest_root

    return stranded.reindex(edges_gdf.index, fill_value=False), len(sizes)


def disconnected_custom_edges(edges_gdf: gpd.GeoDataFrame) -> pd.Series:
    """
    Boolean mask over edges_gdf: custom edges (edges_gdf['custom'] ==
    'yes') that aren't in the same connected component as the rest of
    the network. They export and load without any error, but a router
    can't reach them from anywhere.

    All False if edges_gdf has no 'custom' column.
    """
    return _stranded_custom_edges(edges_gdf)[0]


def assert_all_custom_edges_are_connected(edges_gdf: gpd.GeoDataFrame) -> None:
    """
    Every custom-tagged edge (edges_gdf['custom'] == 'yes') must be in
    the same connected component as the rest of the network. A custom
    edge that only reaches an isolated island - which can happen if a
    junction gets fragmented into several near-duplicate nodes that
    never end up linked to each other - will export and load without
    any error, but will never actually be used by a router, since a
    router can't reach it from anywhere.

    If edges_gdf has no 'custom' column, this is a no-op (nothing to
    check).
    """
    mask, components = _stranded_custom_edges(edges_gdf)
    stranded_rows = edges_gdf[mask]

    if not stranded_rows.empty:
        stranded = sorted(int(n) for n in set(stranded_rows["u"]) | set(stranded_rows["v"]))
        raise NetworkIntegrityError(
            f"{len(stranded)} custom-network node(s) are isolated from the "
            f"main network component and will never be reachable by a "
            f"router: {stranded}. The network has "
            f"{components} disconnected components in total."
        )


def assert_custom_lines_unbroken(
    edges_gdf: gpd.GeoDataFrame,
    input_line_count: int,
) -> None:
    """
    The custom edges on their own (ignoring OSM edges) must form no
    more pieces than there were input lines. More pieces means some
    custom segments were dropped during the build, leaving gaps in a
    road that routers would then detour around via OSM streets.
    assert_all_custom_edges_are_connected can't see this, because the
    pieces are usually still linked through the surrounding network.
    """
    if "custom" not in edges_gdf.columns:
        return

    custom_rows = edges_gdf[edges_gdf["custom"] == "yes"]

    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, v in zip(custom_rows["u"], custom_rows["v"], strict=True):
        parent[find(u)] = find(v)

    pieces = len({find(node) for node in parent})

    if pieces > input_line_count:
        raise NetworkIntegrityError(
            f"custom network has {pieces} separate pieces but only "
            f"{input_line_count} input line(s): some custom segments were "
            f"dropped during the build, leaving gaps."
        )


def validate_network(
    edges_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
) -> None:
    """Run all three structural checks together."""
    assert_all_edges_have_valid_nodes(edges_gdf, points_gdf)
    assert_no_u_equals_v(edges_gdf)
    assert_all_custom_edges_are_connected(edges_gdf)


# ---------------------------------------------------------------------
# Custom feature tag validation (runs BEFORE the build)
# ---------------------------------------------------------------------


# Routable highway values. Anything else (typos, lifecycle values like
# "proposed"/"construction", areas) is rejected: OSM routers ignore it.
KNOWN_HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "road", "busway", "bus_guideway",
    "pedestrian", "track", "footway", "cycleway", "bridleway",
    "path", "steps", "corridor",
}

ACCESS_KEYS = ("access", "vehicle", "motor_vehicle", "motorcar", "foot", "bicycle")
ACCESS_VALUES = {
    "yes", "no", "private", "permissive", "destination", "designated",
    "customers", "delivery", "agricultural", "forestry", "discouraged",
    "permit", "use_sidepath", "dismount", "official", "unknown",
    "restricted", "military", "emergency",
}
ONEWAY_VALUES = {"yes", "no", "-1", "true", "false", "1", "0", "reversible", "alternating"}

# Numeric (km/h), "N mph", "N knots", implicit country codes such as
# "GB:nsl_single", or one of the special values.
MAXSPEED_PATTERN = re.compile(
    r"^(\d+(\.\d+)?( mph| knots)?|[A-Z]{2}:[a-z_]+|none|walk|signals|variable)$"
)

# Columns the pipeline itself uses. A custom property with one of
# these names would corrupt topology (e.g. a 'u' value stops the
# feature being treated as custom).
RESERVED_COLUMNS = {"u", "v", "key", "osmid", "custom", "split", "reversed", "length"}

# Tag keys the pipeline understands (checked, used for routing or
# exported). Used to spot misspelt or truncated attribute names.
KNOWN_TAG_KEYS = mode_tag_keys() | set(BASE_WAY_TAGS) | set(ROUTING_WAY_TAGS)


def _tag_value(value) -> str | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def resolve_custom_tags(
    custom_gdf: gpd.GeoDataFrame,
    default_tags: dict[str, str],
    overwrite: bool = False,
) -> gpd.GeoDataFrame:
    """
    Combine each feature's own tags (its attributes, e.g. a 'highway'
    column) with blanket `default_tags` (a preset and/or network_tags).

    overwrite=False: a feature's own value wins; defaults fill gaps.
    overwrite=True:  defaults replace the feature's value for every key
                     they set. Other attributes are kept.

    All tag values are normalised to strings (50 -> "50").
    """
    custom_gdf = custom_gdf.copy()

    for key, value in default_tags.items():
        if key not in custom_gdf.columns:
            custom_gdf[key] = None
        custom_gdf[key] = custom_gdf[key].astype(object)
        if overwrite:
            custom_gdf[key] = value
        else:
            custom_gdf.loc[custom_gdf[key].isna(), key] = value

    tag_columns = [c for c in custom_gdf.columns if c != custom_gdf.geometry.name]
    for column in tag_columns:
        custom_gdf[column] = custom_gdf[column].map(_tag_value).astype(object)

    return custom_gdf


def check_custom_tags(
    custom_gdf: gpd.GeoDataFrame,
    network_type: str,
    strict: bool = True,
) -> None:
    """
    Check every custom feature's (already resolved) tags follow OSM
    rules and are usable within the network being built.

    Logs which modes the features support. Problems raise
    InvalidTagsError when strict, otherwise are logged as warnings.
    Problems name features by the custom data's index.
    """
    issues = []  # (feature id or None, message)
    notes = set()
    mode_counts = collections.Counter()

    reserved = RESERVED_COLUMNS & set(custom_gdf.columns)
    if reserved:
        issues.append((None, f"custom data has reserved column(s) {sorted(reserved)}; rename them"))

    tag_columns = [c for c in custom_gdf.columns if c != custom_gdf.geometry.name]

    for column in tag_columns:
        suggestion = _suggest_tag_key(column)
        if suggestion:
            log.warning(
                "Attribute %r is not a tag NetworkForge uses - did you mean %r? "
                "(Shapefiles cut names to 10 characters; use GeoPackage.)",
                column, suggestion, extra={"field": column},
            )

    for index, row in custom_gdf.iterrows():
        # _tag_value turns missing values (None/NaN, e.g. a property only
        # some features have) into None; pandas may store that as NaN again.
        tags = {c: row[c] for c in tag_columns if _tag_value(row[c]) is not None}
        label = f"feature {index}"
        highway = tags.get("highway")

        if highway is None:
            issues.append((index, "no highway tag"))
        elif highway not in KNOWN_HIGHWAYS:
            issues.append((index, f"highway={highway!r} is not a routable highway value"))

        maxspeed = tags.get("maxspeed")
        if maxspeed is not None:
            if not MAXSPEED_PATTERN.match(maxspeed):
                issues.append((index, f"maxspeed={maxspeed!r} is not a valid OSM speed"))
            elif maxspeed.replace(".", "").isdigit():
                notes.add(f"maxspeed={maxspeed} has no unit and is read as km/h")

        oneway = tags.get("oneway")
        if oneway is not None and oneway not in ONEWAY_VALUES:
            issues.append((index, f"oneway={oneway!r} is not a valid OSM value"))
        if oneway in {"yes", "true", "1", "-1"}:
            notes.add("oneway direction follows each line's drawing direction")

        lanes = tags.get("lanes")
        if lanes is not None and not (lanes.isdigit() and int(lanes) > 0):
            issues.append((index, f"lanes={lanes!r} must be a positive whole number"))

        for key in ACCESS_KEYS:
            value = tags.get(key)
            if value is not None and value not in ACCESS_VALUES:
                issues.append((index, f"{key}={value!r} is not a valid OSM access value"))

        layer = tags.get("layer")
        if layer is not None and not re.fullmatch(r"-?\d+", layer):
            issues.append((index, f"layer={layer!r} must be a whole number, e.g. 1 or -1"))

        modes = usable_modes(tags)
        if highway in KNOWN_HIGHWAYS:
            mode_counts[", ".join(modes) or "NOTHING"] += 1
        else:
            mode_counts["NOTHING (invalid highway)"] += 1
        log.debug("%s: highway=%s maxspeed=%s -> usable by: %s",
                  label, highway, maxspeed, ", ".join(modes) or "NOTHING")

        if highway in KNOWN_HIGHWAYS:
            if not modes:
                issues.append((index, "tags make it unusable by every mode"))
            elif not allows_mode(tags, network_type):
                issues.append((index, f"not usable in network_type={network_type!r} "
                                      f"(usable by: {', '.join(modes)})"))

    for modes, count in mode_counts.most_common():
        log.info("%d custom feature(s) usable by: %s", count, modes)
    for note in sorted(notes):
        log.info("Note: %s", note)

    if issues:
        error = InvalidTagsError(issues)
        if strict:
            raise error
        features = [i["feature"] for i in error.issues if i["feature"] is not None]
        log.warning("strict=False, continuing anyway: %s", error, extra={"features": features})


def _suggest_tag_key(column: str) -> str | None:
    """A known tag key `column` is probably a misspelling/truncation of."""
    if column in KNOWN_TAG_KEYS:
        return None
    truncated = [key for key in KNOWN_TAG_KEYS if len(column) >= 5 and key.startswith(column)]
    if truncated:
        return truncated[0]
    close = difflib.get_close_matches(column, KNOWN_TAG_KEYS, n=1, cutoff=0.85)
    return close[0] if close else None
