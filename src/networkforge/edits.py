"""
Changing existing streets.

A custom feature with an OSM way id (an `osm_id` or `osmid` attribute,
as on a QuickOSM layer or NetworkForge's own GeoPackage) is not a new
line: it says "this stretch of that way should have these tags". Copy
the street out of such a layer, change an attribute (oneway=yes,
maxspeed=20 mph, access=no) and add it to the custom layer.

What changes:
    - Only tags whose value differs from the way's value in OSM. The
      rest of the feature's attributes are ignored, so a row copied with
      all its attributes changes nothing until one is edited.
    - Only the stretches of the way the feature lies along, in whole
      steps from one OSM node to the next (every junction is a node).
      A feature covering part of a way changes that part.
    - `oneway` follows the direction the feature is drawn, as for new
      lines: reverse the line (or use -1) to reverse the street.
    - Several features may cover the same stretch (rows copied twice,
      overlapping selections) as long as they ask for the same change.
    - A tag can be set, not removed: close a street with access=no, open
      a bus gate with motor_vehicle=yes.
    - `remove=yes` takes the stretch out of the network altogether (its
      other attributes are then ignored): it is in neither the edge table
      nor the OSM file. The way's remaining parts stay; a turn
      restriction that needs the removed part goes with it.

apply_edits() updates the edge table (so the GeoPackage columns and
NetworkForge's own routing follow) and returns the tag changes; export
writes each edited stretch as its own way with those tags and
`nf:modified=yes` (the id of the way it was cut from stays with the
unchanged part). The "before" network is never touched.
"""

import logging

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import shapely

from .errors import InputError, InvalidTagsError
from .osm import OSMSource
from .tags import (
    EDIT_COLUMN,
    EDIT_ID_COLUMN,
    EDIT_ID_COLUMNS,
    MODIFIED_COLUMN,
    REMOVE_COLUMN,
)
from .validation import KNOWN_HIGHWAYS, KNOWN_TAG_KEYS, _tag_value, tag_value_problems

log = logging.getLogger(__name__)

# How OSMnx (and OSM) read oneway values.
ONEWAY_FORWARD = {"yes", "true", "1"}
ONEWAY_BACKWARD = {"-1", "reverse", "t"}

# Two features this close (m) to the same stretch are both on it.
ON_FEATURE = 0.01

# The "change" a feature with remove=yes asks for.
REMOVAL = {REMOVE_COLUMN: "yes"}


def take_edit_ids(custom_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Put the OSM way id of each feature that edits an existing way in one
    column, EDIT_ID_COLUMN (missing for new lines), whichever of
    EDIT_ID_COLUMNS the data uses.
    """
    present = [column for column in EDIT_ID_COLUMNS if column in custom_gdf.columns]
    if not present:
        return custom_gdf

    custom = custom_gdf.copy()
    ids = pd.Series(np.nan, index=custom.index, dtype="float64")
    issues = []
    for column in present:
        for feature, value in custom[column].items():
            text = _tag_value(value)
            if text is None or text == "":
                continue
            # QuickOSM writes "123"; a full_id-style "w123" is accepted too.
            number = text[1:] if text[:1] in "wW" else text
            if number.isdigit() and int(number) > 0:
                ids.at[feature] = int(number)
            else:
                issues.append({"feature": feature,
                               "message": f"{column}={text!r} is not an OSM way id"})
    if issues:
        raise InputError(
            "Some features have an OSM id that isn't a way id (a whole number) - "
            + "; ".join(f"feature {i['feature']}: {i['message']}" for i in issues) + ".",
            guide="changing-existing-streets", issues=issues,
        )

    custom = custom.drop(columns=present)
    custom[EDIT_ID_COLUMN] = ids
    return custom


# What a `remove` attribute may say; anything else is a mistake worth reporting.
REMOVE_YES = {"yes", "true", "1"}
REMOVE_NO = {"no", "false", "0", ""}


def wants_removal(value) -> bool | None:
    """True / False for a `remove` attribute value; None if it isn't a yes or no."""
    text = (_tag_value(value) or "").strip().lower()
    return True if text in REMOVE_YES else False if text in REMOVE_NO else None


def split_edits(custom_gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """(new lines, edits to existing ways) of cleaned custom data."""
    is_edit = (custom_gdf[EDIT_ID_COLUMN].notna() if EDIT_ID_COLUMN in custom_gdf.columns
               else pd.Series(False, index=custom_gdf.index))
    new_lines = custom_gdf[~is_edit].drop(columns=[EDIT_ID_COLUMN], errors="ignore")
    edits = custom_gdf[is_edit]
    if EDIT_ID_COLUMN not in edits.columns:
        edits = edits.assign(**{EDIT_ID_COLUMN: np.nan})

    if REMOVE_COLUMN in custom_gdf.columns:
        # Only an existing street can be removed: a new line marked for
        # removal has most likely lost its OSM id on the way here.
        marked = [feature for feature, value in new_lines[REMOVE_COLUMN].items()
                  if wants_removal(value) is not False]
        if marked:
            raise InputError(
                f"{REMOVE_COLUMN}= is for existing streets: feature(s) "
                f"{', '.join(map(str, marked))} have no OSM id ({' / '.join(EDIT_ID_COLUMNS)}) "
                "saying which street to remove.",
                guide="changing-existing-streets",
                issues=[{"feature": feature, "message": f"{REMOVE_COLUMN} set, but no OSM id"}
                        for feature in marked],
            )
        new_lines = new_lines.drop(columns=[REMOVE_COLUMN])
    return new_lines, edits


def edit_tags(row: pd.Series) -> dict[str, str]:
    """The feature's attributes that are tags NetworkForge knows, as text."""
    tags = {}
    for key, value in row.items():
        text = _tag_value(value)
        if key in KNOWN_TAG_KEYS and text not in (None, ""):
            tags[key] = "yes" if text == "True" else "no" if text == "False" else text
    return tags


def check_edit_tags(edits_gdf: gpd.GeoDataFrame, strict: bool = True) -> None:
    """The values edits would set must be valid, like those of new lines."""
    issues = []
    for feature, row in edits_gdf.drop(columns=edits_gdf.geometry.name).iterrows():
        tags = edit_tags(row)
        problems, _ = tag_value_problems(tags)
        if REMOVE_COLUMN in row and wants_removal(row[REMOVE_COLUMN]) is None:
            problems.append(f"{REMOVE_COLUMN}={_tag_value(row[REMOVE_COLUMN])!r} must be yes or no")
        if "highway" in tags and tags["highway"] not in KNOWN_HIGHWAYS:
            problems.append(f"highway={tags['highway']!r} is not a routable highway value")
        issues += [(feature, problem) for problem in problems]

    if issues:
        error = InvalidTagsError(issues)
        if strict:
            raise error
        log.warning("strict=False, continuing anyway: %s", error,
                    extra={"features": [i["feature"] for i in error.issues]})


def _oneway_state(value: str | None) -> str:
    """'forward', 'backward' or 'both', relative to the direction of the way's nodes."""
    text = (value or "").lower()
    if text in ONEWAY_FORWARD:
        return "forward"
    if text in ONEWAY_BACKWARD:
        return "backward"
    return "both"


def apply_edits(
    edges_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edits_gdf: gpd.GeoDataFrame,
    source: OSMSource,
    tolerance: float,
    bidirectional: bool = False,
    strict: bool = True,
) -> tuple[gpd.GeoDataFrame, dict[int, dict[str, str]], int]:
    """
    Apply the edit features (see the module docstring) to a copy of the
    OSM edge table.

    Returns (edges, changes, removed). Edited edges carry
    MODIFIED_COLUMN="yes" and EDIT_COLUMN=n; changes[n] holds the tags
    edit n sets on its stretch of the way, as OSM tags of that way
    (oneway relative to the way's own direction). `removed` is the
    number of edges taken out.

    edits_gdf: features with EDIT_ID_COLUMN, in the edges' CRS.
    bidirectional: the network type holds every way in both directions
        (OSMnx's walk network), so one-way changes don't alter the edges.
    """
    edges = edges_gdf.copy()
    edges[MODIFIED_COLUMN] = None
    edges[EDIT_COLUMN] = np.nan
    issues = []      # (feature, message): the feature can't be applied
    unchanged = []   # features that differ from OSM in nothing
    node_xy = nodes_gdf.geometry
    edit_columns = [c for c in edits_gdf.columns if c != edits_gdf.geometry.name]
    features = list(edits_gdf.index)

    # 1. What each feature would change, and which edges it lies along.
    wanted: dict[int, dict[str, str]] = {}
    positions: dict[int, dict[int, int]] = {}
    claims = []  # (edge label, distance of its middle from the feature, edit number)
    for number, (feature, row) in enumerate(edits_gdf[edit_columns].iterrows()):
        way_id = int(row[EDIT_ID_COLUMN])
        line = edits_gdf.geometry.loc[feature]
        of_way = (edges["osmid"] == way_id).to_numpy()
        if way_id not in source.ways or not of_way.any():
            issues.append((feature, f"OSM way {way_id} is not in this network (outside the "
                                    "area, not a street, or changed in OSM since the "
                                    "layer was made)"))
            continue

        # The stretches (node to node) of the way the feature lies along.
        middles = shapely.line_interpolate_point(
            edges.geometry.to_numpy()[of_way], 0.5, normalized=True)
        distances = shapely.distance(middles, line)
        covered = edges.index[of_way][distances <= tolerance]
        if len(covered) == 0:
            issues.append((feature, f"doesn't lie along OSM way {way_id}"))
            continue

        refs, current = source.ways[way_id]
        position = {ref: i for i, ref in reversed(list(enumerate(refs)))}
        ends = set(edges.loc[covered, "u"]) | set(edges.loc[covered, "v"])
        ordered = sorted((ref for ref in ends if ref in position), key=position.get)
        along_way = (
            len(ordered) < 2
            or line.project(node_xy.loc[ordered[0]]) <= line.project(node_xy.loc[ordered[-1]])
        )

        change = {}
        if REMOVE_COLUMN in row and wants_removal(row[REMOVE_COLUMN]):
            change = dict(REMOVAL)
        for key, value in ({} if change else edit_tags(row.drop(EDIT_ID_COLUMN))).items():
            if key == "oneway":
                state = _oneway_state(value)
                if not along_way:
                    state = {"forward": "backward", "backward": "forward"}.get(state, state)
                if state != _oneway_state(current.get("oneway")):
                    change[key] = {"forward": "yes", "backward": "-1", "both": "no"}[state]
            elif current.get(key) != value:
                change[key] = value
        if not change:
            unchanged.append(feature)
            continue

        wanted[number], positions[number] = change, position
        claims += zip(covered, distances[distances <= tolerance], [number] * len(covered),
                      strict=True)

    # 2. One feature per edge. A stretch shorter than the tolerance is also
    # within reach of the feature on the next stretch: the feature it lies
    # on (distance 0) wins. Two features really on the same stretch must
    # agree on the change.
    owner: dict = {}
    for label, distance, number in sorted(claims, key=lambda claim: (claim[1], claim[2])):
        if label not in owner:
            owner[label] = (distance, number)
            continue
        best_distance, best = owner[label]
        same_place = distance - best_distance <= ON_FEATURE
        both_stand = number in wanted and best in wanted
        if same_place and both_stand and wanted[number] != wanted[best]:
            way_id = int(edges.at[label, "osmid"])
            issues.append((features[number], f"overlaps feature {features[best]} on OSM "
                                             f"way {way_id} with a different change"))
            wanted.pop(number)

    # 3. Apply.
    changes: dict[int, dict[str, str]] = {}
    drop, rebuilt, removed = [], [], 0
    for number, change in wanted.items():
        rows = pd.Index([label for label, (_, n) in owner.items() if n == number])
        if rows.empty:
            continue
        if change == REMOVAL:
            log.info("Feature %s removes %d edge(s) of OSM way %d", features[number],
                     len(rows), int(edges.at[rows[0], "osmid"]))
            drop.append(rows)
            removed += len(rows)
            continue
        changes[number] = change
        log.info("Feature %s changes %d edge(s) of OSM way %d: %s", features[number], len(rows),
                 int(edges.at[rows[0], "osmid"]),
                 ", ".join(f"{k}={v}" for k, v in change.items()))

        for key, value in change.items():
            if key not in edges.columns:
                edges[key] = None
            if key != "oneway":
                edges[key] = edges[key].astype(object)
                edges.loc[rows, key] = value
        edges.loc[rows, MODIFIED_COLUMN] = "yes"
        edges.loc[rows, EDIT_COLUMN] = number
        if "oneway" in change and not bidirectional:
            drop.append(rows)
            rebuilt += _in_direction(edges.loc[rows], positions[number],
                                     _oneway_state(change["oneway"]))
    if drop:
        edges = pd.concat([edges.drop(drop[0].append(drop[1:])), *rebuilt], ignore_index=True)

    if issues:
        error = InputError(
            "Some features that change an existing street can't be applied - "
            + "; ".join(f"feature {feature}: {message}" for feature, message in issues) + ".",
            guide="changing-existing-streets",
            issues=[{"feature": feature, "message": message} for feature, message in issues],
        )
        if strict:
            raise error
        log.warning("strict=False, skipping them: %s", error,
                    extra={"features": [feature for feature, _ in issues]})
    if unchanged:
        log.warning("%d feature(s) with an OSM id change nothing: their tags are the same "
                    "as the way's in OpenStreetMap (features %s).", len(unchanged),
                    ", ".join(map(str, unchanged)), extra={"features": unchanged})

    if not changes:
        edges = edges.drop(columns=[MODIFIED_COLUMN, EDIT_COLUMN])
    return edges, changes, removed


def _in_direction(stretch: gpd.GeoDataFrame, position: dict[int, int], state: str) -> list:
    """
    The edges of some stretches of one way, rebuilt for a new one-way
    state the way OSMnx holds them: a one-way stretch is one edge in the
    direction of travel; a two-way stretch is two, the second marked
    reversed.
    """
    if "reversed" in stretch:
        stretch = stretch[stretch["reversed"] != True]  # noqa: E712 - one row per stretch

    # Each row oriented along the way (from the earlier node to the later).
    against = stretch["u"].map(position).to_numpy() > stretch["v"].map(position).to_numpy()
    forward = stretch.copy()
    forward.loc[against, ["u", "v"]] = stretch.loc[against, ["v", "u"]].to_numpy()
    geometries = forward.geometry.to_numpy().copy()
    geometries[against] = shapely.reverse(geometries[against])
    forward = forward.set_geometry(geometries, crs=stretch.crs)

    backward = forward.copy()
    backward[["u", "v"]] = forward[["v", "u"]].to_numpy()
    backward = backward.set_geometry(shapely.reverse(geometries), crs=stretch.crs)

    forward["reversed"], backward["reversed"] = False, state == "both"
    forward["oneway"] = backward["oneway"] = state != "both"
    return {"forward": [forward], "backward": [backward], "both": [forward, backward]}[state]


def is_bidirectional(network_type: str) -> bool:
    return network_type in ox.settings.bidirectional_network_types
