"""
Traffic for Valhalla: time bands by road class, and for single streets.

    bands = read_traffic_settings("traffic.toml")
    streets = street_bands(geopandas.read_file("streets.gpkg"))     # optional
    add_traffic("after.osm.pbf", bands, "after-traffic.tar", streets=streets)

A band says how much slower some streets are at some times, as a share
of each street's normal speed (the speed Valhalla gives it from its tags):

    [[band]]
    name = "Morning peak"
    days = "Mo-Fr"
    time = "07:00-09:30"
    roads = ["motorway", "trunk", "primary", "secondary"]
    factor = 0.6

Valhalla keeps time-of-day speeds in its own graph, not in the OSM file
(no OSM tag describes a typical week of speeds, and Valhalla stores
`maxspeed:conditional` without routing by it). So this builds Valhalla's
graph from a NetworkForge PBF, finds the graph's road segments for every
street a band covers, gives each segment a week of speeds (2,016
five-minute values, Sunday 00:00 first), loads them with Valhalla's own
`valhalla_add_predicted_traffic`, checks they took, and packs the graph
into a tar file that Valhalla (and the QGIS Network Analyst plugin's
"From Tar") routes on directly.

Single streets: a line layer, one row per street and time band
(`days`, `time`, `factor`, optional `direction` both/forward and `name`).
A row's line is matched onto the road segments it is drawn along
(Valhalla's own map matcher), so it can be a stretch of a long way or a
new street; with an OSM way id (`osm_id` / `osmid`) as well, only that
way's stretches count, and a way id without a line is the whole way. A
segment with rows of its own ignores the road-class bands.

Routes only see the bands when they are asked for a departure or
arrival time. Without a time, every street keeps its normal speed: each
segment's free-flow speed is its normal speed and no daytime speed is
set (Valhalla would otherwise use a daytime speed for requests without
a time).

Needs pyvalhalla (`pip install "networkforge[traffic]"`); it is the only
module that imports it, and only when called.
"""

import csv
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import osmium

from .errors import InputError, NetworkIntegrityError, feature_id
from .paths import readable
from .tags import EDIT_ID_COLUMNS
from .validation import KNOWN_HIGHWAYS

log = logging.getLogger(__name__)

GUIDE = "traffic.md"

# Valhalla's week: 5-minute buckets, bucket 0 = Sunday 00:00 (local time).
BUCKET_MINUTES = 5
BUCKETS_PER_DAY = 24 * 60 // BUCKET_MINUTES
BUCKETS_PER_WEEK = 7 * BUCKETS_PER_DAY
DAYS = {"su": 0, "mo": 1, "tu": 2, "we": 3, "th": 4, "fr": 5, "sa": 6}

# A band's factor: a share of the normal speed. Below 5% a street is
# effectively closed (close it with access tags instead); above 200%
# is almost certainly a typo.
MIN_FACTOR, MAX_FACTOR = 0.05, 2.0

# Locations per /locate request.
LOCATE_BATCH = 1000

# How many loaded segments to check after loading (all, up to this).
VERIFY_SAMPLE = 500

ALL_ROADS = "all"

Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class TrafficBand:
    """Streets of `roads` run at `factor` of their normal speed on `days` during `time`."""

    name: str
    days: tuple[int, ...]          # 0 = Sunday ... 6 = Saturday
    start: int                     # minutes after midnight
    end: int                       # minutes after midnight; <= start runs past midnight
    roads: frozenset[str]          # highway values (links included), or {"all"}
    factor: float

    def covers(self, highway: str) -> bool:
        return ALL_ROADS in self.roads or _road_class(highway) in self.roads

    def buckets(self) -> np.ndarray:
        """The week's 5-minute buckets this band covers."""
        first, last = self.start // BUCKET_MINUTES, self.end // BUCKET_MINUTES
        if last <= first:
            last += BUCKETS_PER_DAY  # runs past midnight into the next day
        steps = np.arange(first, last)
        starts = np.array(self.days) * BUCKETS_PER_DAY
        return (starts[:, None] + steps[None, :]).ravel() % BUCKETS_PER_WEEK


DIRECTIONS = ("both", "forward")
STREET_FIELDS = ("days", "time", "factor", "direction", "name")

# A drawn line claims a road segment if it runs along at least this share
# of it (a line copied from the network layer covers its own stretch
# fully; one drawn by hand may overshoot a little into the next).
MIN_COVERED = 0.5
MATCH_RADIUS_M = 15
DENSIFY_DEGREES = 0.0001        # about 10 m


@dataclass(frozen=True)
class StreetBand:
    """One street (or stretch) at `factor` of its normal speed on `days` during `time`."""

    feature: object                       # the row's id, for messages
    way: int | None                       # OSM way id: that way (or its stretch along `line`)
    line: tuple[tuple[float, float], ...] | None   # (lon, lat), drawn direction
    direction: str                        # "both" or "forward" (drawn / way direction)
    band: TrafficBand


@dataclass
class TrafficResult:
    """What add_traffic did."""

    output: Path
    ways: int = 0                        # streets given traffic
    segments: int = 0                    # Valhalla directed edges given traffic
    by_road: dict[str, int] = field(default_factory=dict)   # road class -> streets
    bands: list[str] = field(default_factory=list)
    streets_not_found: list[int] = field(default_factory=list)  # OSM way ids
    single_streets: int = 0              # street rows used
    single_segments: int = 0             # segments those rows set
    unmatched: list = field(default_factory=list)  # street rows that matched nothing


# ---------------------------------------------------------------- settings

def read_traffic_settings(path: str | Path) -> list[TrafficBand]:
    """Bands from a .toml or .json file: a list of tables under `band`."""
    path = Path(path)
    if not path.is_file():
        raise InputError(f"The traffic settings file doesn't exist: {path}", guide=GUIDE)
    try:
        if path.suffix.lower() == ".json":
            settings = json.loads(path.read_text(encoding="utf-8"))
        else:
            settings = tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InputError(f"Can't read the traffic settings in {path}: {exc}",
                         guide=GUIDE) from exc
    return parse_bands(settings.get("band") if isinstance(settings, dict) else None)


def parse_bands(rows) -> list[TrafficBand]:
    """Check and convert band tables ({name, days, time, roads, factor})."""
    if not isinstance(rows, list) or not rows:
        raise InputError("The traffic settings need at least one [[band]].",
                         guide=f"{GUIDE}#settings")
    problems, bands = [], []
    for number, row in enumerate(rows, start=1):
        label = f"band {number}"
        if not isinstance(row, dict):
            problems.append(f"{label} isn't a table of settings")
            continue
        label = f"band {number} ({row['name']})" if row.get("name") else label
        unknown = set(row) - {"name", "days", "time", "roads", "factor"}
        if unknown:
            problems.append(f"{label}: unknown setting(s) {', '.join(sorted(unknown))}")
        values, failed = {}, False
        for key, check, default in (("days", _days, "Mo-Su"), ("time", _time_range, None),
                                    ("roads", _roads, None), ("factor", _factor, None)):
            try:
                values[key] = check(row.get(key, default))
            except ValueError as exc:
                problems.append(f"{label}: {exc}")
                failed = True
        if not failed:
            start, end = values["time"]
            bands.append(TrafficBand(str(row.get("name") or f"Band {number}"), values["days"],
                                     start, end, values["roads"], values["factor"]))
    if problems:
        raise InputError("Traffic settings problem(s):\n  - " + "\n  - ".join(problems),
                         guide=f"{GUIDE}#settings",
                         issues=[{"feature": None, "message": p} for p in problems])
    return bands


def _days(text) -> tuple[int, ...]:
    """'Mo-Fr', 'Sa,Su', 'Mo-Su', 'Fr-Mo' (wraps) -> day numbers, Sunday = 0."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("days must be like 'Mo-Fr' or 'Sa,Su'")
    days = []
    for part in text.replace(" ", "").lower().split(","):
        first, _, last = part.partition("-")
        if first not in DAYS or (last and last not in DAYS):
            raise ValueError(f"days {text!r}: use Mo Tu We Th Fr Sa Su, e.g. 'Mo-Fr' or 'Sa,Su'")
        a = DAYS[first]
        b = DAYS[last] if last else a
        # Monday-first order, so 'Mo-Su' is the whole week and 'Fr-Mo' wraps.
        a7, b7 = (a - 1) % 7, (b - 1) % 7
        span = range(a7, b7 + 1) if a7 <= b7 else [*range(a7, 7), *range(0, b7 + 1)]
        days.extend((d + 1) % 7 for d in span)
    return tuple(sorted(set(days)))


_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def _time_range(text) -> tuple[int, int]:
    """'07:00-09:30' -> (420, 570); '22:00-06:00' runs past midnight; '00:00-24:00' all day."""
    match = _TIME.match(text) if isinstance(text, str) else None
    if not match:
        raise ValueError("time must be like '07:00-09:30'")
    h1, m1, h2, m2 = map(int, match.groups())
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if h1 > 23 or m1 > 59 or m2 > 59 or end > 24 * 60:
        raise ValueError(f"time {text!r} isn't a time of day")
    if start % BUCKET_MINUTES or end % BUCKET_MINUTES:
        raise ValueError(f"time {text!r}: times must be on a 5-minute mark (Valhalla's steps)")
    if start == end:
        raise ValueError(f"time {text!r} is empty")
    return start, end % (24 * 60) if end == 24 * 60 and start == 0 else end


def _roads(value) -> frozenset[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError("roads must list highway values, e.g. ['primary', 'secondary'], "
                         "or be 'all'")
    roads = {str(v).strip().lower() for v in value}
    if ALL_ROADS in roads:
        return frozenset({ALL_ROADS})
    unknown = sorted(r for r in roads if r not in KNOWN_HIGHWAYS)
    if unknown:
        raise ValueError(f"unknown road class(es) {', '.join(unknown)}; use highway values "
                         f"such as motorway, trunk, primary, secondary, tertiary, residential")
    return frozenset(_road_class(r) for r in roads)


def _factor(value) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("factor must be a number, e.g. 0.6 for 60% of the normal speed")
    if not MIN_FACTOR <= value <= MAX_FACTOR:
        raise ValueError(f"factor {value} must be between {MIN_FACTOR} and {MAX_FACTOR} "
                         "(a share of the normal speed)")
    return float(value)


def _road_class(highway: str) -> str:
    """'primary_link' counts as 'primary'."""
    return highway.removesuffix("_link")


def street_bands(layer) -> list[StreetBand]:
    """
    Single-street bands from a line layer (a GeoDataFrame indexed by the
    feature ids messages should use). Fields: days (default every day),
    time, factor, direction (both / forward; default both), name; and
    optionally osm_id or osmid. Every problem is listed at once.
    """
    if layer.crs is None:
        raise InputError("The single-streets layer has no coordinate reference system.",
                         guide=f"{GUIDE}#single-streets")
    layer = layer.to_crs("EPSG:4326")
    id_column = next((c for c in EDIT_ID_COLUMNS if c in layer.columns), None)
    unknown = [c for c in layer.columns
               if c not in (*STREET_FIELDS, *EDIT_ID_COLUMNS, layer.geometry.name)]
    if unknown:
        log.info("Single streets: fields not used for traffic: %s", ", ".join(map(str, unknown)))
    problems, streets = [], []
    for feature, row in layer.iterrows():
        def value(key, default=None, row=row):
            v = row.get(key)
            return default if v is None or (isinstance(v, float) and np.isnan(v)) or v == "" \
                else v
        values, failed = {}, False
        for key, check, default in (("days", _days, "Mo-Su"), ("time", _time_range, None),
                                    ("factor", _number, None)):
            try:
                values[key] = check(value(key, default))
            except ValueError as exc:
                problems.append((feature, str(exc)))
                failed = True
        direction = str(value("direction", "both")).strip().lower()
        if direction not in DIRECTIONS:
            problems.append((feature, f"direction {direction!r} must be both or forward"))
            failed = True
        way = value(id_column) if id_column else None
        if way is not None:
            try:
                way = int(float(way))
            except (TypeError, ValueError):
                problems.append((feature, f"{id_column} {way!r} isn't an OSM way id"))
                failed = True
        geometry = row.geometry
        lines = []
        if geometry is not None and not geometry.is_empty:
            parts = getattr(geometry, "geoms", [geometry])
            lines = [tuple(p.coords) for p in parts if p.geom_type == "LineString"]
            if len(lines) != len(parts):
                problems.append((feature, f"is a {geometry.geom_type}; single streets are lines"))
                failed = True
        elif way is None:
            problems.append((feature, "has neither a line nor an OSM way id"))
            failed = True
        if failed:
            continue
        start, end = values["time"]
        band = TrafficBand(str(value("name", f"Street {feature_id(feature)}")), values["days"],
                           start, end, frozenset(), values["factor"])
        if lines:
            streets.extend(StreetBand(feature, way, line, direction, band) for line in lines)
        else:
            streets.append(StreetBand(feature, way, None, direction, band))
    if problems:
        raise InputError(
            f"{len(problems)} single-street problem(s):\n  - " + "\n  - ".join(
                f"feature {feature_id(f)}: {m}" for f, m in problems),
            guide=f"{GUIDE}#single-streets",
            issues=[{"feature": f, "message": m} for f, m in problems])
    return streets


def _number(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("factor must be a number, e.g. 0.6 for 60% of the normal speed") from None
    return _factor(value)


def week_factors(bands: Iterable[TrafficBand], highway: str) -> np.ndarray | None:
    """
    The week of speed factors for a street of this highway class (2,016
    values, Sunday 00:00 first), or None if no band covers it. Where
    bands overlap, the slowest wins.
    """
    return _combine([band for band in bands if band.covers(highway)])


def _combine(bands: list[TrafficBand]) -> np.ndarray | None:
    """The bands' week of factors (slowest wins where they overlap), or None if none."""
    if not bands:
        return None
    factors = np.full(BUCKETS_PER_WEEK, np.inf, dtype=np.float32)
    for band in bands:
        idx = band.buckets()
        factors[idx] = np.minimum(factors[idx], band.factor)
    factors[np.isinf(factors)] = 1.0  # no band at that time: normal speed
    return factors


# ---------------------------------------------------------------- the build

def add_traffic(pbf: str | Path, bands: list[TrafficBand], out: str | Path,
                streets: list[StreetBand] | None = None,
                progress: Progress | None = None) -> TrafficResult:
    """
    Build Valhalla's graph from `pbf`, put the road-class `bands` and the
    single `streets` on it and write it as the tar file `out`.
    """
    valhalla, bin_dir, get_config, GraphId, compress, encode, GraphUtils = _import_valhalla()
    pbf, out = Path(pbf), Path(out)
    streets = streets or []
    if not bands and not streets:
        raise InputError("No traffic to add: give road-class bands, single streets or both.",
                         guide=GUIDE)
    if not pbf.is_file():
        raise InputError(f"The OSM file doesn't exist: {pbf}", guide=GUIDE)
    if out.suffix.lower() != ".tar":
        raise InputError(f"The traffic output must be a .tar file, got {out.name}", guide=GUIDE)
    if not out.resolve().parent.is_dir():
        raise InputError(f"The folder for the traffic output doesn't exist: {out.parent}")
    if out.resolve() == pbf.resolve():
        raise InputError("The traffic output would overwrite the OSM file.")
    step = _steps(progress, 6)
    result = TrafficResult(output=out, bands=[b.name for b in bands])

    step("Reading streets")
    by_id = {s.way for s in streets if s.way is not None}
    ways = _ways(pbf, bands, by_id)
    missing = sorted(by_id - set(ways))
    if missing:
        rows = [s.feature for s in streets if s.way in missing]
        log.warning("%d single street(s) name an OSM way that isn't in %s (%s); they get no "
                    "traffic.", len(rows), pbf.name, ", ".join(map(str, missing[:10])),
                    extra={"features": rows})
        result.unmatched.extend(rows)
        streets = [s for s in streets if s.way not in missing]

    with tempfile.TemporaryDirectory(prefix="networkforge-traffic-") as work:
        work = Path(work)
        step("Building Valhalla's graph")
        config_path = _build_graph(pbf, work, bin_dir, get_config)
        actor = valhalla.Actor(str(config_path))

        step("Finding the streets' road segments")
        segments, not_found = _segments(actor, ways)
        banded = {w for w, way in ways.items() if any(b.covers(way.highway) for b in bands)}
        result.streets_not_found = sorted(not_found & banded)
        if result.streets_not_found:
            log.warning("Valhalla has no road segment for %d street(s) in a traffic band "
                        "(it doesn't route on them); they get no traffic.",
                        len(result.streets_not_found))
        claims = _claim(actor, GraphUtils(str(config_path)), GraphId, streets, segments, result)

        step("Writing the speeds")
        traffic_dir = work / "traffic"
        rows = _write_speed_files(segments, ways, bands, claims, traffic_dir,
                                  GraphId, compress, encode)

        step("Loading the speeds into the graph")
        if rows:
            _run([str(bin_dir / "valhalla_add_predicted_traffic"), "-c", str(config_path),
                  str(traffic_dir)], "load the traffic")
            _verify(valhalla.Actor(str(config_path)), segments, rows)
        else:
            log.warning("No road segment got traffic: the tar is the network without traffic.")

        step("Packing the graph")
        _pack(config_path, out)

    result.segments = len(rows)
    by_class = {segments[e].way for e in rows if e not in claims and segments[e].way in banded}
    result.ways = len({segments[e].way for e in rows})
    counts: dict[str, int] = defaultdict(int)
    for way in by_class:
        counts[_road_class(ways[way].highway)] += 1
    result.by_road = dict(sorted(counts.items()))
    result.single_segments = len(claims)
    return result


@dataclass
class _Way:
    highway: str
    points: list[tuple[float, float]]   # (lon, lat) midpoints of each node-to-node step


def _ways(pbf: Path, bands: list[TrafficBand], wanted: set[int]) -> dict[int, _Way]:
    """Ways a band covers, plus the `wanted` way ids: id -> highway and where to look."""
    ways = {}
    with readable(pbf) as name:
        for way in osmium.FileProcessor(name, osmium.osm.NODE | osmium.osm.WAY).with_locations():
            if not isinstance(way, osmium.osm.Way):
                continue
            highway = way.tags.get("highway", "")
            if way.id not in wanted and not (highway and any(b.covers(highway) for b in bands)):
                continue
            try:
                coords = [(n.location.lon, n.location.lat) for n in way.nodes]
            except osmium.InvalidLocationError:
                continue  # a way cut at the extract's edge
            if len(coords) < 2:
                continue
            xy = np.array(coords)
            mids = (xy[:-1] + xy[1:]) / 2
            ways[way.id] = _Way(highway, [tuple(p) for p in mids])
    return ways


def _build_graph(pbf: Path, work: Path, bin_dir: Path, get_config) -> Path:
    tile_dir = work / "tiles"
    tile_dir.mkdir()
    config = get_config(tile_extract="", tile_dir=str(tile_dir))
    config["mjolnir"]["concurrency"] = 1
    config_path = work / "valhalla.json"
    config_path.write_text(json.dumps(config))
    # Valhalla reads OSM files through libosmium, so it gets the same help
    # with non-ASCII paths on Windows as osmium does.
    with readable(pbf) as name:
        _run([str(bin_dir / "valhalla_build_tiles"), "-c", str(config_path), name],
             "build a graph from the OSM file")
    if not any(tile_dir.rglob("*.gph")):
        raise InputError(f"Valhalla found no streets to route on in {pbf.name}.", guide=GUIDE)
    return config_path


@dataclass(frozen=True)
class _Segment:
    way: int
    speed: int                      # Valhalla's normal speed, km/h
    probe: tuple[float, float]      # a (lon, lat) where /locate finds it
    forward: bool                   # runs in the way's own direction


def _segments(actor, ways: dict[int, _Way]) -> tuple[dict[int, _Segment], set[int]]:
    """
    Valhalla's directed edges for each way, by edge id. Found with /locate
    at the middle of every step between two of the way's nodes, so every
    segment of the way is hit (both directions).
    """
    points = [(way, p) for way, street in ways.items() for p in street.points]
    segments: dict[int, _Segment] = {}
    for batch, located in _locate(actor, [p for _, p in points], points):
        for (way, point), found in zip(batch, located, strict=True):
            for edge in found.get("edges") or []:
                edge_id = edge["edge_id"]["value"]
                speed = edge["edge"]["speeds"]["default"]
                if edge["edge_info"]["way_id"] == way and speed > 0 and edge_id not in segments:
                    segments[edge_id] = _Segment(way, int(speed), point,
                                                 bool(edge["edge"]["forward"]))
    found_ways = {s.way for s in segments.values()}
    return segments, set(ways) - found_ways


def _locate(actor, points: list[tuple[float, float]], items: list):
    """(items, /locate results) in batches of LOCATE_BATCH points."""
    for i in range(0, len(points), LOCATE_BATCH):
        located = actor.locate({
            "locations": [{"lon": lon, "lat": lat, "radius": 1}
                          for lon, lat in points[i : i + LOCATE_BATCH]],
            "verbose": True,
            "costing": "none",
        })
        yield items[i : i + LOCATE_BATCH], located


def _claim(actor, graph, GraphId, streets: list[StreetBand], segments: dict[int, _Segment],
           result: TrafficResult) -> dict[int, list[TrafficBand]]:
    """
    The road segments each single street sets: edge id -> its bands.
    Segments a drawn line finds that no band covers are added to `segments`.
    """
    claims: dict[int, list[TrafficBand]] = defaultdict(list)
    used = set()
    unmatched = []
    for street in streets:
        if street.line is not None:
            # Along the line; with a way id too, only that way's stretches
            # (as when changing an existing street).
            edges = [e for e in _match(actor, graph, GraphId, street, segments)
                     if street.way is None or segments[e].way == street.way]
        else:
            edges = [e for e, s in segments.items() if s.way == street.way
                     and (street.direction == "both" or s.forward)]
        if not edges:
            unmatched.append(street.feature)
            continue
        used.add(street.feature)
        for edge in edges:
            claims[edge].append(street.band)
    unmatched = [f for f in dict.fromkeys(unmatched) if f not in used]
    if unmatched:
        log.warning("%d single street(s) run along no road segment Valhalla drives on (draw "
                    "them along the street, over at least half of each stretch); they get "
                    "no traffic.", len(unmatched), extra={"features": unmatched})
        result.unmatched.extend(unmatched)
    result.single_streets = len(used)
    return claims


def _match(actor, graph, GraphId, street: StreetBand, segments: dict[int, _Segment]) -> list[int]:
    """Edges a drawn line runs along (each over at least MIN_COVERED of it)."""
    from shapely import LineString, segmentize

    line = street.line
    shapes = [line] if street.direction == "forward" else [line, tuple(reversed(line))]
    edges = []
    for shape in shapes:
        dense = segmentize(LineString(shape), DENSIFY_DEGREES).coords
        try:
            matched = actor.trace_attributes({
                "shape": [{"lon": x, "lat": y} for x, y in dense],
                "shape_match": "map_snap",
                "costing": "auto",
                "trace_options": {"search_radius": MATCH_RADIUS_M},
                "filters": {"attributes": ["edge.id", "edge.way_id", "edge.speed",
                                           "edge.forward"], "action": "include"},
            })
        except RuntimeError:
            continue  # no drivable street along it in this direction
        for edge in matched.get("edges") or []:
            covered = edge.get("target_percent_along", 1) - edge.get("source_percent_along", 0)
            if covered < MIN_COVERED or not edge.get("speed"):
                continue
            edge_id = edge["id"]
            if edge_id not in segments:
                shape_ll = LineString(graph.get_edge_shape(GraphId(edge_id)))
                middle = shape_ll.interpolate(0.5, normalized=True)
                segments[edge_id] = _Segment(edge["way_id"], int(edge["speed"]),
                                             (middle.x, middle.y), bool(edge["forward"]))
            edges.append(edge_id)
    return list(dict.fromkeys(edges))


def _write_speed_files(segments: dict[int, _Segment], ways: dict[int, _Way], bands,
                       claims: dict[int, list[TrafficBand]], folder: Path,
                       GraphId, compress, encode) -> dict[int, int]:
    """
    One CSV per graph tile (in a tree mirroring the tiles), one row per
    segment: id, free-flow speed, daytime speed (0 = none), the encoded
    week. A segment a single street claims takes that street's bands
    only. Returns edge id -> free-flow speed for every row written.
    """
    by_class: dict[str, np.ndarray | None] = {}
    rows: dict[int, int] = {}
    files: dict[Path, list] = defaultdict(list)
    for edge_id, segment in sorted(segments.items()):
        if edge_id in claims:
            factors = _combine(claims[edge_id])
        else:
            way = ways.get(segment.way)
            highway = way.highway if way else ""
            if highway not in by_class:
                by_class[highway] = week_factors(bands, highway) if highway else None
            factors = by_class[highway]
        if factors is None:
            continue
        week = np.maximum(segment.speed * factors, 1.0).astype(np.float32)
        gid = GraphId(edge_id)
        path = (folder / os.fspath(gid)).with_suffix(".csv")
        # Daytime speed 0 = not set: requests without a time use free-flow.
        files[path].append([str(gid), segment.speed, 0, encode(compress(week))])
        rows[edge_id] = segment.speed
    for path, lines in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        # "\n" only: with "\r\n" Valhalla silently drops every row.
        with path.open("w", newline="", encoding="ascii") as f:
            csv.writer(f, lineterminator="\n").writerows(lines)
    return rows


def _verify(actor, segments: dict[int, _Segment], rows: dict[int, int]) -> None:
    """
    Valhalla's loader reports nothing and drops a bad row without a word,
    so look a sample of the segments up again and check they took.
    """
    ids = sorted(rows)
    sample = ids[:: max(1, len(ids) // VERIFY_SAMPLE)][:VERIFY_SAMPLE]
    missing = set(sample)
    for batch, located in _locate(actor, [segments[e].probe for e in sample], sample):
        for edge_id, found in zip(batch, located, strict=True):
            for edge in found.get("edges") or []:
                if edge["edge_id"]["value"] != edge_id:
                    continue
                speeds = edge["edge"]["speeds"]
                if speeds.get("predicted") and speeds.get("free_flow") == rows[edge_id]:
                    missing.discard(edge_id)
    if missing:
        ways = sorted({segments[e].way for e in missing})
        raise NetworkIntegrityError(
            f"Valhalla didn't take the traffic speeds for {len(missing)} of {len(sample)} "
            f"checked road segments (OSM ways {', '.join(map(str, ways[:10]))}"
            f"{' ...' if len(ways) > 10 else ''}).")
    log.info("Checked %d of %d road segments: all have their traffic speeds.",
             len(sample), len(rows))


def _pack(config_path: Path, out: Path) -> None:
    config = json.loads(config_path.read_text())
    config["mjolnir"]["tile_extract"] = str(out.resolve())
    pack_config = config_path.with_name("pack.json")
    pack_config.write_text(json.dumps(config))
    _run([sys.executable, "-m", "valhalla.valhalla_build_extract", "-c", str(pack_config),
          "-O"], "pack the graph into a tar file")
    if not out.is_file():
        raise NetworkIntegrityError(f"Valhalla didn't write the tar file {out}.")


def _run(command: list[str], what: str) -> None:
    done = subprocess.run(command, capture_output=True, text=True, check=False,
                          env=_valhalla_env(Path(command[0])))
    if done.returncode != 0:
        detail = (done.stderr or done.stdout)[-2000:].strip()
        raise NetworkIntegrityError(
            f"Valhalla could not {what} (exit code {done.returncode})"
            + (f":\n{detail}" if detail else "."))


def _valhalla_env(program: Path) -> dict[str, str]:
    """
    The environment for one of pyvalhalla's programs. On Windows their
    libraries are in `pyvalhalla.libs` beside the package, which they only
    find through PATH (exit code 3221225781, "DLL not found", without it).
    """
    env = dict(os.environ)
    if os.name == "nt":
        libraries = program.parent.parent.parent / "pyvalhalla.libs"
        if program.parent.name == "bin" and libraries.is_dir():
            env["PATH"] = os.pathsep.join([str(libraries), env.get("PATH", "")])
    return env


def _steps(progress: Progress | None, total: int):
    count = 0

    def step(message: str) -> None:
        nonlocal count
        count += 1
        log.info("[%d/%d] %s", count, total, message)
        if progress:
            progress(count, total, message)
    return step


def _import_valhalla():
    """pyvalhalla, imported only when traffic is used."""
    try:
        from valhalla._scripts import PYVALHALLA_BIN_DIR
        from valhalla.baldr import GraphId
        from valhalla.baldr.utils import (
            GraphUtils,
            compress_speed_buckets,
            encode_compressed_speeds,
        )
        from valhalla.config import get_config

        import valhalla
    except ImportError as exc:
        raise InputError('Traffic needs pyvalhalla: pip install "networkforge[traffic]"',
                         guide=GUIDE) from exc
    return (valhalla, Path(PYVALHALLA_BIN_DIR), get_config, GraphId,
            compress_speed_buckets, encode_compressed_speeds, GraphUtils)
