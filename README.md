# NetworkForge

[![tests](https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml/badge.svg)](https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml)
[![coverage](https://codecov.io/gh/Yibbzz/networkforge/graph/badge.svg)](https://codecov.io/gh/Yibbzz/networkforge)

Integrate custom (proposed) roads, cycleways and paths into an existing
OpenStreetMap network, following OSM access rules, so the result can be
routed for any transport mode and compared with the original network.

## What it doesn't do

NetworkForge builds **street networks** (driving, cycling, walking). It
is not a full transport model:

- **No public transport.** Trains, trams, metro, buses and ferries are
  not modelled: their travel times depend on timetables, not just the
  physical network. Railway and tram tracks are not included. The PBF
  NetworkForge exports can be combined with GTFS timetables in tools
  such as [r5py](https://r5py.readthedocs.io) or
  [OpenTripPlanner](https://www.opentripplanner.org) for that.
- **No traffic simulation.** No congestion, demand or signal timing:
  travel times come from speed limits (or default speeds per road type).
- **No turn restrictions yet.** OSM turn restrictions ("no right turn")
  are relations, which aren't downloaded or exported.
- **Its own routing checks are simplified.** The OSMnx-based routing in
  the scripts ignores e.g. `oneway:bicycle`; routers such as GraphHopper
  and Valhalla read those tags from the exported file.

## Install

From a release (see [Releases](https://github.com/Yibbzz/networkforge/releases)):

```bash
pip install "networkforge @ git+https://github.com/Yibbzz/networkforge@v0.2.0"
```

For development, clone the repo and run `uv sync`.

## Command line

```bash
# Check a custom layer's tags (seconds, no download)
networkforge check --custom proposed.gpkg --preset primary_road

# Build: every custom line a 40 mph primary road; OSM PBF + GeoPackage out
networkforge build --extent extent.gpkg --custom proposed.gpkg \
    --preset primary_road --tag maxspeed="40 mph" \
    --out network.osm.pbf --baseline-out baseline.osm.pbf --gpkg network.gpkg

# Area given as W,S,E,N, OSM data from a local extract
networkforge build --bbox -4.60,54.10,-4.40,54.25 --custom proposed.gpkg \
    --osm-source isle-of-man.osm.pbf --out network.osm.pbf

networkforge presets              # list presets and who can use them
networkforge build --help         # every option
```

Add `--json` to get progress, results and errors as JSON lines on stdout,
for programs driving the CLI. Exit codes: `0` success, `2` bad usage,
`3` unusable input, `4` OSM download failed, `5` structural check failed,
`1` anything else.

## Outputs

| Output | For | What's in it |
|---|---|---|
| `.osm.pbf` / `.osm` (`--out`, `write_osm`) | Routing engines: **Valhalla**, **GraphHopper**, OpenTripPlanner | Standard OSM data with every routing tag; the engine applies its own profiles |
| GeoPackage (`--gpkg`, `write_gpkg`) | **QGIS**: viewing, styling, and its network analysis tools | `nodes` and `edges` layers; edges have ready-made analysis columns |

GeoPackage `edges` columns, besides the OSM tags (`highway`, `maxspeed`, ...):

| Column | Meaning |
|---|---|
| `car`, `bike`, `walk` | Whether that mode may use the edge (same access rules as the engine; `car` excludes service roads) |
| `speed_kph` | Car speed: `maxspeed`, else a default for the road type |
| `length_m` | Length in metres |
| `car_minutes`, `bike_minutes`, `walk_minutes` | Travel time (bikes 15 km/h, walking 5 km/h) |
| `car_direction`, `bike_direction` | `forward`, `backward` or `both`, relative to the line's direction. Bikes follow `oneway:bicycle=no`. Walking is always both ways. |
| `custom` | `yes` on your custom edges |

Each street is one row, and every edge ends exactly on its nodes, so QGIS
connects the network correctly. For example, a 10-minute drive area in QGIS:

1. Filter the `edges` layer to `"car"` (layer Properties > Source > Query Builder).
2. Processing > Network analysis > **Service area (from point)**:
   path type *Fastest*, travel cost `600` (seconds),
   *Direction field* `car_direction` with values `forward` / `backward` / `both`,
   *Speed field* `speed_kph`.

Use `bike` / `bike_direction` (or `walk`, with no direction field) for other
modes, and `*_minutes` as the cost field in plugins such as QNEAT3.

## Python

```python
import logging
import geopandas as gpd
from networkforge import build_network, write_osm

logging.basicConfig(level=logging.INFO)  # show build progress

bbox = gpd.read_file("extent.gpkg")
custom = gpd.read_file("proposed.gpkg")

# Blanket: every custom line is a 40 mph primary road
nodes, edges = build_network(bbox, custom, preset="primary_road",
                             network_tags={"maxspeed": "40 mph"})

# Per feature: each line's own attributes (highway, maxspeed, bridge, ...)
nodes, edges = build_network(bbox, custom)

write_osm(nodes, edges, "network.osm.pbf")  # or .osm, .osm.gz, .osm.bz2
```

**Large areas:** the existing network comes from the shared Overpass
API, limited to boxes of 1,000 km². For bigger areas (or faster,
repeatable builds) download an extract, e.g. from
[Geofabrik](https://download.geofabrik.de), and pass
`osm_source="region.osm.pbf"`. See [docs/osm-data.md](docs/osm-data.md).

`write_osm` picks the format from the file name. PBF is OpenStreetMap's
compressed binary format (open, [documented on the OSM wiki](https://wiki.openstreetmap.org/wiki/PBF_Format)),
several times smaller than XML and what routers such as GraphHopper
and Valhalla read. Exports keep the tags routers need: access
restrictions, one-way exceptions, cycle infrastructure, surface,
vehicle limits, and barrier nodes such as bollards.

A feature's own attributes win over a preset unless you pass
`overwrite_tags=True`. Who may use each way follows OSM rules: the
way type plus the access hierarchy (`motorcar` > `motor_vehicle` >
`vehicle` > `access`, and `bicycle` / `foot` for bikes and walking). Custom lines join every street they cross,
except motorways, bridges, tunnels and other layers, which they pass
over or under. Tags are checked against OSM rules before anything is
downloaded.

**[Tagging guide](docs/tagging-guide.md)**: presets, every supported
attribute, recipes (bypass, cycle route, bus gate, bridge, slip road),
and what each error message means.

Errors are subclasses of `networkforge.NetworkForgeError` (`InputError`,
`InvalidTagsError`, `NoIntersectionError`, `OSMDownloadError`,
`NetworkIntegrityError`). Progress goes to the `networkforge` logger,
and to an optional `progress(step, total, description)` callback.

The exported network contains OpenStreetMap data, © OpenStreetMap
contributors, available under the
[Open Database License](https://www.openstreetmap.org/copyright).

## Tests

```bash
uv run pytest                     # offline suite (unit, synthetic grid, property-based)
uv run pytest --cov               # ... with a coverage report
uv run pytest -m network          # live OSM tests in real cities (slow, needs internet)
uv run ruff check .               # lint
```

| Folder | What | Speed |
|---|---|---|
| `tests/unit/` | tag rules, CRS selection | milliseconds |
| `tests/integration/` | full pipeline on a hand-built street grid, plus Hypothesis property tests | seconds |
| `tests/live/` | real OSM data in Edinburgh, Amsterdam, Manchester | minutes |

CI runs lint and the offline suite on every push and pull request, and
the live tests plus a 500-example property run nightly.

## Releasing

1. Update `version` in `pyproject.toml` and add a `## [x.y.z]` section to
   [CHANGELOG.md](CHANGELOG.md).
2. Commit, then tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The release workflow checks the tag matches the version, runs the tests,
builds the package and publishes a GitHub Release with the changelog
notes and the wheel attached.

## Scripts

- `scripts/get_test_data.py`: build the demo network in `tests/data`
- `scripts/route_test.py`: compare routes with and without the custom network
- `scripts/diagnose_custom_network.py`: report on how a custom network was integrated
