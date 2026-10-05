"""
Command-line interface: `networkforge build | check | presets | info`.

    networkforge build --extent area.gpkg --custom plan.gpkg --preset primary_road \\
        --tag maxspeed="40 mph" --out network.osm.pbf --gpkg after.gpkg \\
        --baseline-gpkg before.gpkg

Output streams: stdout carries the result - readable text by default,
or with --json one JSON object per line, for programs driving the CLI.
stderr always carries human-readable progress, warnings and errors.

JSON events (a stable interface: other tools depend on these shapes):

    {"event": "progress", "step": 2, "total": 13, "message": "Downloading OSM network"}
    {"event": "warning", "message": "...", "features": [3, 7]}        # features/fields optional
    {"event": "done", "outputs": {"osm": "network.osm.pbf"}, "nodes": 131459, ...,
     "custom_edges": 12, "modified_edges": 3, "removed_edges": 2}
    (edge counts are streets, one per GeoPackage row: a two-way street is
    one edge, not one each way)
    {"event": "error", "type": "InvalidTagsError", "message": "...", "guide": "...",
     "issues": [{"feature": 3, "message": "maxspeed='fast' is not a valid OSM speed"}],
     "problems": ["feature 3: maxspeed='fast' is not a valid OSM speed"]}

Features are identified by --id-field (e.g. a GeoPackage's fid), or by
row number when it isn't given.

Exit codes: 0 success, 1 unexpected error, 2 bad command-line usage,
3 unusable input (InputError and subclasses), 4 OSM download failed,
5 built network failed a structural check.
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from .edits import check_edit_tags, split_edits, take_edit_ids
from .errors import (
    InputError,
    InvalidTagsError,
    NetworkForgeError,
    NetworkIntegrityError,
    OSMDownloadError,
    feature_id,
)
from .export import GPKG_ANALYSIS_COLUMNS, reverse_copies, write_gpkg, write_osm
from .inputs import (
    MAX_OVERPASS_AREA_KM2,
    check_feature_ids,
    clean_custom_data,
    drop_reserved_columns,
    match_attribute_names,
)
from .modes import MODES, usable_modes
from .network import JOIN_AT, build_network
from .osm import NETWORK_TYPES
from .presets import PRESETS, preset_tags
from .tags import EDIT_ID_COLUMN, EDIT_ID_COLUMNS, MODIFIED_COLUMN, REMOVE_COLUMN, REMOVED_ATTR
from .validation import (
    ACCESS_KEYS,
    ACCESS_VALUES,
    KNOWN_HIGHWAYS,
    KNOWN_TAG_KEYS,
    LIMIT_KEYS,
    LIMIT_PATTERN,
    MAXSPEED_PATTERN,
    ONEWAY_VALUES,
    check_custom_tags,
    resolve_custom_tags,
)

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_INPUT, EXIT_DOWNLOAD, EXIT_INTEGRITY = range(6)

log = logging.getLogger("networkforge.cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(_join_negative_bbox(sys.argv[1:] if argv is None else argv))
    if args.command is cmd_build:
        _check_build_arguments(parser, args)
    emit = _emitter(args.json)
    _configure_logging(args.verbose, args.quiet, emit if args.json else None)

    try:
        return args.command(args, emit)
    except BrokenPipeError:
        # The reader went away (e.g. `| head`): stop quietly, as the
        # Python docs recommend, instead of a traceback on exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_ERROR
    except NetworkForgeError as exc:
        emit_error(emit, exc)
        return _exit_code(exc)
    except Exception as exc:  # noqa: BLE001 - report anything, don't dump a traceback
        log.debug("Unexpected error", exc_info=True)
        emit_error(emit, exc)
        return EXIT_ERROR


# ---------------------------------------------------------------- commands

def cmd_build(args, emit) -> int:
    if not (args.out or args.gpkg or args.baseline_out or args.baseline_gpkg):
        raise InputError("Nothing to write: give --out, --gpkg, --baseline-out or --baseline-gpkg.")

    bbox = None if args.no_osm else _read_extent(args)
    custom = _read_custom(args)
    baseline = bool(args.baseline_out or args.baseline_gpkg)

    def progress(step: int, total: int, message: str) -> None:
        emit({"event": "progress", "step": step, "total": total, "message": message})

    result = build_network(
        bbox,
        custom,
        network_tags=_tags(args.tag),
        network_type=args.network_type,
        return_source_osm=baseline,
        snap_tolerance=args.snap_tolerance,
        strict=not args.no_strict,
        preset=args.preset,
        overwrite_tags=args.overwrite_tags,
        osm_source=args.osm_source,
        standalone=args.no_osm,
        join_at=args.join_at,
        progress=progress,
    )
    nodes, edges = result[:2]

    outputs = {}
    if args.out:
        write_osm(nodes, edges, args.out)
        outputs["osm"] = str(args.out)
    if args.baseline_out:
        write_osm(result[2], result[3], args.baseline_out)
        outputs["baseline_osm"] = str(args.baseline_out)
    if args.gpkg:
        write_gpkg(nodes, edges, args.gpkg)
        outputs["gpkg"] = str(args.gpkg)
    if args.baseline_gpkg:
        write_gpkg(result[2], result[3], args.baseline_gpkg)
        outputs["baseline_gpkg"] = str(args.baseline_gpkg)

    # One per street, as in the GeoPackage: OSMnx holds a two-way street
    # as two edges, one each way.
    streets = edges[~reverse_copies(edges)]
    custom_edges = int((streets["custom"] == "yes").sum()) if "custom" in streets else 0
    modified_edges = (int((streets[MODIFIED_COLUMN] == "yes").sum())
                      if MODIFIED_COLUMN in streets else 0)
    removed_edges = int(edges.attrs.get(REMOVED_ATTR, 0))
    emit({"event": "done", "outputs": outputs, "nodes": len(nodes), "edges": len(streets),
          "custom_edges": custom_edges, "modified_edges": modified_edges,
          "removed_edges": removed_edges},
         text=f"Done: {len(nodes):,} nodes, {len(streets):,} edges ({custom_edges:,} custom, "
              f"{modified_edges:,} changed, {removed_edges:,} removed). "
              f"Wrote {', '.join(outputs.values())}")
    return EXIT_OK


def cmd_check(args, emit) -> int:
    custom = _read_custom(args)
    if args.extent or args.bbox:
        custom = clean_custom_data(custom, _read_extent(args), strict=True)
    else:
        check_feature_ids(custom)
        custom = take_edit_ids(match_attribute_names(custom))
        custom = drop_reserved_columns(custom, quiet=EDIT_ID_COLUMN in custom.columns)

    # Changes to existing streets are checked for valid values only: which
    # way they change is known once the network is downloaded (build).
    custom, edits = split_edits(custom)
    check_edit_tags(edits, strict=True)

    blanket = {**(preset_tags(args.preset) if args.preset else {}), **_tags(args.tag)}
    if not custom.empty:
        custom = resolve_custom_tags(custom, blanket, overwrite=args.overwrite_tags)
        check_custom_tags(custom, args.network_type, strict=True)

    tag_columns = [c for c in custom.columns if c != custom.geometry.name]
    modes = Counter(
        ", ".join(usable_modes({c: row[c] for c in tag_columns if row[c] is not None}))
        or "nothing"
        for _, row in custom.iterrows()
    )
    summary = "; ".join(f"{count} usable by {m}" for m, count in modes.most_common())
    if len(edits):
        summary = "; ".join(filter(None, [summary, f"{len(edits)} change existing streets"]))
    emit({"event": "done", "features": len(custom) + len(edits), "modes": dict(modes),
          "edits": len(edits)},
         text=f"OK: {len(custom) + len(edits)} feature(s) - {summary}")
    return EXIT_OK


def cmd_info(args, emit) -> int:
    """Everything a front end needs to build its UI from the engine."""
    info = {
        "event": "info",
        "version": version("networkforge"),
        "presets": {name: {"tags": tags, "modes": usable_modes(tags)}
                    for name, tags in PRESETS.items()},
        "network_types": list(NETWORK_TYPES),
        "modes": list(MODES),
        "max_overpass_area_km2": MAX_OVERPASS_AREA_KM2,
        "osm_formats": [".osm", ".osm.pbf", ".pbf", ".osm.gz", ".osm.bz2"],
        "gpkg_edge_columns": list(GPKG_ANALYSIS_COLUMNS),
        "edit_id_fields": list(EDIT_ID_COLUMNS),
        "remove_field": REMOVE_COLUMN,
        "standalone": True,
        "join_at": list(JOIN_AT),
        "tag_keys": sorted(KNOWN_TAG_KEYS),
        "tag_values": {
            "highway": sorted(KNOWN_HIGHWAYS),
            "oneway": sorted(ONEWAY_VALUES),
            **{key: sorted(ACCESS_VALUES) for key in ACCESS_KEYS},
        },
        "tag_patterns": {"maxspeed": MAXSPEED_PATTERN.pattern, "lanes": r"^[1-9]\d*$",
                         "layer": r"^-?\d+$",
                         **{key: LIMIT_PATTERN.pattern for key in LIMIT_KEYS}},
    }
    emit(info, text=(
        f"networkforge {info['version']}\n"
        f"presets: {', '.join(PRESETS)}\n"
        f"network types: {', '.join(NETWORK_TYPES)}\n"
        f"Overpass area limit: {MAX_OVERPASS_AREA_KM2:,} km2 (larger areas need --osm-source)\n"
        f"highway values: {', '.join(sorted(KNOWN_HIGHWAYS))}"
    ))
    return EXIT_OK


def cmd_presets(args, emit) -> int:
    for name, tags in PRESETS.items():
        modes = usable_modes(tags)
        emit({"event": "preset", "name": name, "tags": tags, "modes": modes},
             text=f"{name:20s} {', '.join(f'{k}={v}' for k, v in tags.items()):55s} "
                  f"{', '.join(modes)}")
    return EXIT_OK


# ---------------------------------------------------------------- arguments

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="networkforge",
        description="Build and edit routable street networks: add your own roads, cycleways "
                    "and paths to OpenStreetMap, change or remove existing streets, or "
                    "build a network from your own lines alone.",
        epilog="Tagging guide: docs/tagging-guide.md. Large areas: docs/osm-data.md.",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {version('networkforge')}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="write JSON lines to stdout (for programs) instead of text")
    common.add_argument("-v", "--verbose", action="store_true", help="show debug detail")
    common.add_argument("-q", "--quiet", action="store_true", help="only show warnings and errors")
    commands = parser.add_subparsers(title="commands", required=True)

    build = commands.add_parser("build", parents=[common],
                                help="build the combined network and write it out",
                                description="Build the OSM + custom network, or with --no-osm "
                                            "a network of the custom lines alone.")
    _add_extent(build, required=False)
    _add_custom(build)
    build.add_argument("--no-osm", action="store_true",
                       help="build a network from the custom lines alone, without "
                            "OpenStreetMap: no area, download or before network")
    build.add_argument("--join-at", choices=JOIN_AT, default="crossings",
                       help="with --no-osm, where lines join each other: wherever they "
                            "cross (default), or only at vertices they share")
    build.add_argument("--osm-source", type=Path, metavar="FILE",
                       help="local OSM extract (.osm.pbf etc.) instead of the Overpass API; "
                            "required for areas over 1,000 km2")
    build.add_argument("--snap-tolerance", type=float, default=1.0, metavar="METRES",
                       help="how close custom lines must come to join the network (default 1)")
    build.add_argument("--no-strict", action="store_true",
                       help="warn and drop unusable features instead of failing")
    build.add_argument("--out", type=Path, metavar="FILE",
                       help="OSM output: .osm, .osm.pbf, .osm.gz or .osm.bz2")
    build.add_argument("--baseline-out", type=Path, metavar="FILE",
                       help="also write the untouched OSM network, for before/after comparisons")
    build.add_argument("--baseline-gpkg", type=Path, metavar="FILE",
                       help="also write the untouched OSM network as a GeoPackage for QGIS "
                            "(same layers and columns as --gpkg), for before/after")
    build.add_argument("--gpkg", type=Path, metavar="FILE",
                       help="GeoPackage for QGIS: 'nodes' and 'edges' layers, edges with "
                            "network-analysis columns (car/bike/walk, speed, times, direction)")
    build.set_defaults(command=cmd_build)

    check = commands.add_parser("check", parents=[common],
                                help="check custom data and tags without downloading anything",
                                description="Validate custom features and their tags.")
    _add_extent(check, required=False)
    _add_custom(check)
    check.set_defaults(command=cmd_check)

    presets = commands.add_parser("presets", parents=[common], help="list the tag presets")
    presets.set_defaults(command=cmd_presets)

    info = commands.add_parser(
        "info", parents=[common],
        help="version, presets, valid tag values and limits (for front ends)")
    info.set_defaults(command=cmd_info)

    return parser


def _add_extent(parser, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--extent", type=Path, metavar="FILE",
                       help="vector file whose extent is the area (any CRS)")
    group.add_argument("--bbox", metavar="W,S,E,N",
                       help="area as WGS84 longitude/latitude: west,south,east,north")
    parser.add_argument("--extent-layer", metavar="NAME", help="layer in the extent file")


def _add_custom(parser) -> None:
    parser.add_argument("--custom", type=Path, required=True, metavar="FILE",
                        help="custom lines (GeoPackage, GeoJSON, ...)")
    parser.add_argument("--custom-layer", metavar="NAME", help="layer in the custom file")
    parser.add_argument("--id-field", metavar="NAME",
                        help="attribute that identifies features in warnings and errors "
                             "(e.g. fid); default: row number")
    parser.add_argument("--preset", choices=list(PRESETS), metavar="NAME",
                        help="blanket tag preset (list them with: networkforge presets)")
    parser.add_argument("--tag", action="append", default=[], metavar="KEY=VALUE",
                        help="blanket tag, repeatable; beats the preset")
    parser.add_argument("--overwrite-tags", action="store_true",
                        help="blanket tags replace features' own values instead of filling gaps")
    parser.add_argument("--network-type", choices=NETWORK_TYPES, default="all",
                        help="OSM network to download (default all: every mode)")


def _check_build_arguments(parser, args) -> None:
    """build needs an area unless --no-osm, which in turn has no OSM options."""
    if not args.no_osm:
        if not (args.extent or args.bbox):
            parser.error("one of the arguments --extent --bbox is required (or --no-osm)")
        if args.join_at != "crossings":
            parser.error("--join-at is for --no-osm networks")
        return
    for given, flag in ((args.extent or args.bbox, "--extent / --bbox"),
                        (args.osm_source, "--osm-source"),
                        (args.baseline_out, "--baseline-out"),
                        (args.baseline_gpkg, "--baseline-gpkg")):
        if given:
            parser.error(f"{flag} can't be used with --no-osm (there is no OpenStreetMap "
                         "network or before network)")


# ---------------------------------------------------------------- helpers

def _join_negative_bbox(argv: Sequence[str]) -> list[str]:
    """
    `--bbox -4.6,54.1,...` would make argparse read the western
    longitude as an option; turn it into `--bbox=-4.6,54.1,...`.
    """
    argv = list(argv)
    for i, arg in enumerate(argv[:-1]):
        if arg == "--bbox" and argv[i + 1].startswith("-"):
            argv[i : i + 2] = [f"--bbox={argv[i + 1]}"]
            break
    return argv


def _tags(pairs: list[str]) -> dict[str, str]:
    tags = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise InputError(f"--tag must look like KEY=VALUE, got {pair!r}")
        tags[key.strip()] = value.strip()
    return tags


def _read_layer(path: Path, layer: str | None, label: str, **kwargs) -> gpd.GeoDataFrame:
    if not path.is_file():
        raise InputError(f"The {label} file doesn't exist: {path}")
    try:
        return gpd.read_file(path, layer=layer, **kwargs)
    except Exception as exc:  # noqa: BLE001 - GDAL raises many types
        raise InputError(f"Can't read the {label} from {path}: {exc}") from exc


def _read_custom(args) -> gpd.GeoDataFrame:
    """The custom layer, indexed by --id-field so messages name those ids."""
    custom = _read_layer(args.custom, args.custom_layer, "custom data")
    field = args.id_field
    if not field:
        return custom
    if field in custom.columns:
        return custom.set_index(field)
    # A GeoPackage's fid isn't a column unless asked for.
    with_fid = _read_layer(args.custom, args.custom_layer, "custom data", fid_as_index=True)
    if field.lower() == "fid":
        return with_fid.rename_axis(field)
    attributes = ", ".join(map(str, custom.columns.drop(custom.geometry.name)))
    raise InputError(f"--id-field {field!r} isn't an attribute of the custom data. "
                     f"Attributes: {attributes}")


def _read_extent(args) -> gpd.GeoDataFrame:
    if args.bbox:
        try:
            west, south, east, north = (float(v) for v in args.bbox.split(","))
        except ValueError:
            raise InputError(f"--bbox must be W,S,E,N numbers, got {args.bbox!r}") from None
        if not (west < east and south < north):
            raise InputError("--bbox needs west < east and south < north")
        return gpd.GeoDataFrame(geometry=[box(west, south, east, north)], crs="EPSG:4326")
    extent = _read_layer(args.extent, args.extent_layer, "extent")
    return gpd.GeoDataFrame(geometry=[box(*extent.total_bounds)], crs=extent.crs)


def _configure_logging(verbose: bool, quiet: bool, emit=None) -> None:
    """Readable logs on stderr; with --json, warnings also become JSON events."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr, force=True)
    library = logging.getLogger("networkforge")
    for handler in [h for h in library.handlers if isinstance(h, _WarningEvents)]:
        library.removeHandler(handler)  # from an earlier main() in this process
    if emit is not None:
        library.addHandler(_WarningEvents(emit))


class _WarningEvents(logging.Handler):
    """Turns networkforge warnings into {"event": "warning", ...} JSON events."""

    def __init__(self, emit):
        super().__init__(level=logging.WARNING)
        self.emit_event = emit

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno != logging.WARNING:  # errors are reported as error events
            return
        event = {"event": "warning", "message": record.getMessage()}
        if getattr(record, "features", None):
            event["features"] = [feature_id(f) for f in record.features]
        if getattr(record, "fields", None) or getattr(record, "field", None):
            event["fields"] = list(getattr(record, "fields", None) or [record.field])
        self.emit_event(event)


def _emitter(as_json: bool):
    """emit(event, text=None): a JSON line on stdout, or readable text."""
    def emit(event: dict, text: str | None = None) -> None:
        if as_json:
            print(json.dumps(event), flush=True)
        elif event["event"] == "progress":
            pass  # the library already logs each step
        elif text is not None:
            print(text, flush=True)
    return emit


def emit_error(emit, exc: Exception) -> None:
    event = {"event": "error", "type": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, NetworkForgeError) and exc.guide:
        event["guide"] = exc.guide
    if isinstance(exc, NetworkForgeError) and exc.issues:
        event["issues"] = exc.issues
    if isinstance(exc, InvalidTagsError):
        event["problems"] = exc.problems
    emit(event, text=None)
    log.error("Error (%s): %s", type(exc).__name__, exc)


def _exit_code(exc: NetworkForgeError) -> int:
    if isinstance(exc, InputError):
        return EXIT_INPUT
    if isinstance(exc, OSMDownloadError):
        return EXIT_DOWNLOAD
    if isinstance(exc, NetworkIntegrityError):
        return EXIT_INTEGRITY
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
