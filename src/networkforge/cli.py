"""
Command-line interface: `networkforge build | check | presets`.

    networkforge build --extent area.gpkg --custom plan.gpkg --preset primary_road \\
        --tag maxspeed="40 mph" --out network.osm.pbf --gpkg network.gpkg

Human-readable progress goes to stderr. With --json, stdout carries one
JSON object per line instead, for programs driving the CLI:

    {"event": "progress", "step": 2, "total": 13, "message": "Downloading OSM network"}
    {"event": "done", "outputs": {"osm": "network.osm.pbf"}, "nodes": 131459, ...}
    {"event": "error", "type": "InvalidTagsError", "message": "...", "guide": "...",
     "problems": ["feature 0: ..."]}

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

from .errors import (
    InputError,
    InvalidTagsError,
    NetworkForgeError,
    NetworkIntegrityError,
    OSMDownloadError,
)
from .export import write_gpkg, write_osm
from .inputs import clean_custom_data
from .modes import usable_modes
from .network import build_network
from .osm import NETWORK_TYPES
from .presets import PRESETS, preset_tags
from .validation import check_custom_tags, resolve_custom_tags

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_INPUT, EXIT_DOWNLOAD, EXIT_INTEGRITY = range(6)

log = logging.getLogger("networkforge.cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(_join_negative_bbox(sys.argv[1:] if argv is None else argv))
    _configure_logging(args.verbose, args.quiet)
    emit = _emitter(args.json)

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
    if not (args.out or args.gpkg):
        raise InputError("Nothing to write: give --out and/or --gpkg.")

    bbox = _read_extent(args)
    custom = _read_layer(args.custom, args.custom_layer, "custom data")

    def progress(step: int, total: int, message: str) -> None:
        emit({"event": "progress", "step": step, "total": total, "message": message})

    result = build_network(
        bbox,
        custom,
        network_tags=_tags(args.tag),
        network_type=args.network_type,
        return_source_osm=bool(args.baseline_out),
        snap_tolerance=args.snap_tolerance,
        strict=not args.no_strict,
        preset=args.preset,
        overwrite_tags=args.overwrite_tags,
        osm_source=args.osm_source,
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

    custom_edges = int((edges["custom"] == "yes").sum()) if "custom" in edges else 0
    emit({"event": "done", "outputs": outputs, "nodes": len(nodes), "edges": len(edges),
          "custom_edges": custom_edges},
         text=f"Done: {len(nodes):,} nodes, {len(edges):,} edges ({custom_edges:,} custom). "
              f"Wrote {', '.join(outputs.values())}")
    return EXIT_OK


def cmd_check(args, emit) -> int:
    custom = _read_layer(args.custom, args.custom_layer, "custom data")
    if args.extent or args.bbox:
        custom = clean_custom_data(custom, _read_extent(args), strict=True)

    blanket = {**(preset_tags(args.preset) if args.preset else {}), **_tags(args.tag)}
    custom = resolve_custom_tags(custom, blanket, overwrite=args.overwrite_tags)
    check_custom_tags(custom, args.network_type, strict=True)

    tag_columns = [c for c in custom.columns if c != custom.geometry.name]
    modes = Counter(
        ", ".join(usable_modes({c: row[c] for c in tag_columns if row[c] is not None}))
        or "nothing"
        for _, row in custom.iterrows()
    )
    summary = "; ".join(f"{count} usable by {m}" for m, count in modes.most_common())
    emit({"event": "done", "features": len(custom), "modes": dict(modes)},
         text=f"OK: {len(custom)} feature(s) - {summary}")
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
        description="Integrate custom roads, cycleways and paths into OpenStreetMap networks.",
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
                                description="Build the OSM + custom network.")
    _add_extent(build, required=True)
    _add_custom(build)
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
    parser.add_argument("--preset", choices=list(PRESETS), metavar="NAME",
                        help="blanket tag preset (list them with: networkforge presets)")
    parser.add_argument("--tag", action="append", default=[], metavar="KEY=VALUE",
                        help="blanket tag, repeatable; beats the preset")
    parser.add_argument("--overwrite-tags", action="store_true",
                        help="blanket tags replace features' own values instead of filling gaps")
    parser.add_argument("--network-type", choices=NETWORK_TYPES, default="all",
                        help="OSM network to download (default all: every mode)")


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


def _read_layer(path: Path, layer: str | None, label: str) -> gpd.GeoDataFrame:
    if not path.is_file():
        raise InputError(f"The {label} file doesn't exist: {path}")
    try:
        return gpd.read_file(path, layer=layer)
    except Exception as exc:  # noqa: BLE001 - GDAL raises many types
        raise InputError(f"Can't read the {label} from {path}: {exc}") from exc


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


def _configure_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr, force=True)


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
