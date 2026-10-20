"""
Traffic for Valhalla: time bands by road class.

    bands = read_traffic_settings("traffic.toml")
    add_traffic("after.osm.pbf", bands, "after-traffic.tar")

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

from .errors import InputError, NetworkIntegrityError
from .paths import readable
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


@dataclass
class TrafficResult:
    """What add_traffic did."""

    output: Path
    ways: int = 0                        # streets given traffic
    segments: int = 0                    # Valhalla directed edges given traffic
    by_road: dict[str, int] = field(default_factory=dict)   # road class -> streets
    bands: list[str] = field(default_factory=list)
    streets_not_found: list[int] = field(default_factory=list)  # OSM way ids


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


def week_factors(bands: Iterable[TrafficBand], highway: str) -> np.ndarray | None:
    """
    The week of speed factors for a street of this highway class (2,016
    values, Sunday 00:00 first), or None if no band covers it. Where
    bands overlap, the slowest wins.
    """
    factors = None
    for band in bands:
        if band.covers(highway):
            if factors is None:
                factors = np.full(BUCKETS_PER_WEEK, np.inf, dtype=np.float32)
            idx = band.buckets()
            factors[idx] = np.minimum(factors[idx], band.factor)
    if factors is not None:
        factors[np.isinf(factors)] = 1.0  # no band at that time: normal speed
    return factors


# ---------------------------------------------------------------- the build

def add_traffic(pbf: str | Path, bands: list[TrafficBand], out: str | Path,
                progress: Progress | None = None) -> TrafficResult:
    """
    Build Valhalla's graph from `pbf`, put the bands on it and write it as
    the tar file `out`. Returns what was done.
    """
    valhalla, bin_dir, get_config, GraphId, compress, encode = _import_valhalla()
    pbf, out = Path(pbf), Path(out)
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
    streets = _streets(pbf, bands)
    if not streets:
        log.warning("No street in %s is in a traffic band's road classes: the tar has no "
                    "traffic.", pbf.name)

    with tempfile.TemporaryDirectory(prefix="networkforge-traffic-") as work:
        work = Path(work)
        step("Building Valhalla's graph")
        config_path = _build_graph(pbf, work, bin_dir, get_config)
        actor = valhalla.Actor(str(config_path))

        step("Finding the streets' road segments")
        segments, not_found = _segments(actor, streets)
        result.streets_not_found = sorted(not_found)
        if not_found:
            log.warning("Valhalla has no road segment for %d street(s) in a traffic band "
                        "(it doesn't route on them); they get no traffic.", len(not_found))

        step("Writing the speeds")
        traffic_dir = work / "traffic"
        rows = _write_speed_files(segments, streets, bands, traffic_dir,
                                  GraphId, compress, encode)

        step("Loading the speeds into the graph")
        if rows:
            _run([str(bin_dir / "valhalla_add_predicted_traffic"), "-c", str(config_path),
                  str(traffic_dir)], "load the traffic")
            _verify(valhalla.Actor(str(config_path)), segments, rows)

        step("Packing the graph")
        _pack(config_path, out)

    result.segments = len(rows)
    ways = {segments[e].way for e in rows}
    result.ways = len(ways)
    counts: dict[str, int] = defaultdict(int)
    for way in ways:
        counts[_road_class(streets[way].highway)] += 1
    result.by_road = dict(sorted(counts.items()))
    return result


@dataclass
class _Street:
    highway: str
    points: list[tuple[float, float]]   # (lon, lat) midpoints of each node-to-node step


def _streets(pbf: Path, bands: list[TrafficBand]) -> dict[int, _Street]:
    """Ways a band covers: way id -> highway and where to look for its segments."""
    streets = {}
    with readable(pbf) as name:
        for way in osmium.FileProcessor(name, osmium.osm.NODE | osmium.osm.WAY).with_locations():
            if not isinstance(way, osmium.osm.Way):
                continue
            highway = way.tags.get("highway")
            if not highway or not any(b.covers(highway) for b in bands):
                continue
            try:
                coords = [(n.location.lon, n.location.lat) for n in way.nodes]
            except osmium.InvalidLocationError:
                continue  # a way cut at the extract's edge
            if len(coords) < 2:
                continue
            xy = np.array(coords)
            mids = (xy[:-1] + xy[1:]) / 2
            streets[way.id] = _Street(highway, [tuple(p) for p in mids])
    return streets


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


def _segments(actor, streets: dict[int, _Street]) -> tuple[dict[int, _Segment], set[int]]:
    """
    Valhalla's directed edges for each street, by edge id. Found with
    /locate at the middle of every step between two of the way's nodes,
    so every segment of the way is hit (both directions).
    """
    points = [(way, p) for way, street in streets.items() for p in street.points]
    segments: dict[int, _Segment] = {}
    for batch, located in _locate(actor, [p for _, p in points], points):
        for (way, point), found in zip(batch, located, strict=True):
            for edge in found.get("edges") or []:
                edge_id = edge["edge_id"]["value"]
                speed = edge["edge"]["speeds"]["default"]
                if edge["edge_info"]["way_id"] == way and speed > 0 and edge_id not in segments:
                    segments[edge_id] = _Segment(way, int(speed), point)
    found_ways = {s.way for s in segments.values()}
    return segments, set(streets) - found_ways


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


def _write_speed_files(segments: dict[int, _Segment], streets: dict[int, _Street], bands,
                       folder: Path, GraphId, compress, encode) -> dict[int, int]:
    """
    One CSV per graph tile (in a tree mirroring the tiles), one row per
    segment: id, free-flow speed, daytime speed (0 = none), the encoded
    week. Returns edge id -> free-flow speed for every row written.
    """
    by_class: dict[str, np.ndarray | None] = {}
    rows: dict[int, int] = {}
    files: dict[Path, list] = defaultdict(list)
    for edge_id, segment in sorted(segments.items()):
        highway = streets[segment.way].highway
        if highway not in by_class:
            by_class[highway] = week_factors(bands, highway)
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


def _run(command: list[str], what: str, cwd: Path | None = None) -> None:
    done = subprocess.run(command, capture_output=True, text=True, check=False, cwd=cwd)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout)[-2000:]
        raise NetworkIntegrityError(f"Valhalla could not {what}:\n{detail}")


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
        from valhalla.baldr.utils import compress_speed_buckets, encode_compressed_speeds
        from valhalla.config import get_config

        import valhalla
    except ImportError as exc:
        raise InputError('Traffic needs pyvalhalla: pip install "networkforge[traffic]"',
                         guide=GUIDE) from exc
    return (valhalla, Path(PYVALHALLA_BIN_DIR), get_config, GraphId,
            compress_speed_buckets, encode_compressed_speeds)
