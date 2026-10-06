# NetworkForge

[![tests](https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml/badge.svg)](https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml)
[![coverage](https://codecov.io/gh/Yibbzz/networkforge/graph/badge.svg)](https://codecov.io/gh/Yibbzz/networkforge)

Build and edit routable street networks from GIS layers. Add your own
roads, cycleways and paths to OpenStreetMap, change or remove existing
streets by editing attributes, or build a network from your own lines
alone - and get the result as OSM PBF for routers (tested in Valhalla)
and as a GeoPackage for QGIS.

## What it does

- **Add** proposed roads, cycleways and paths to the OpenStreetMap
  network. They join the streets they cross, except where one is a
  bridge, a tunnel or on another layer.
- **Change** existing streets: copy one into your layer and edit an
  attribute to make it one-way, close it, pedestrianise it or change its
  speed limit ([how](docs/tagging-guide.md#changing-existing-streets)).
- **Remove** existing streets (`remove=yes`).
- **Ban or force turns** at junctions, drawn as short lines through the
  junction ([how](docs/tagging-guide.md#turn-restrictions)).
- **Build a network from your own lines alone**, with no OpenStreetMap
  ([how](docs/tagging-guide.md#a-network-of-your-own-lines)).
- **Before and after**: the untouched network and the changed one, to
  compare routes, travel times and catchments.
- **Describe streets with OpenStreetMap tags** as plain attributes
  (`highway`, `maxspeed`, `oneway`, `maxheight`, `surface`, ...), or pick
  a preset for the whole layer. Values are checked before anything is
  built.

The OSM PBF output is tested by routing on it with
[Valhalla](https://valhalla.github.io/valhalla/): the "before" file routes
like OpenStreetMap itself, and each rule above is checked for cars,
buses, lorries, bikes and walking. [docs/network-analyst.md](docs/network-analyst.md)
compares the result with ArcGIS Network Analyst's network dataset,
feature by feature.

## What it doesn't do

NetworkForge builds **street networks** (driving, cycling, walking). It
is not a full transport model:

- **No public transport.** Trains, trams, metro and buses are not
  modelled: their travel times depend on timetables, not just the
  physical network. Railway and tram tracks are not included. Ferry
  routes are written to the OSM file for routers, but are not in the
  GeoPackage. The PBF can be combined with GTFS timetables in tools such
  as [r5py](https://r5py.readthedocs.io) or
  [OpenTripPlanner](https://www.opentripplanner.org).
- **Existing streets can be re-tagged or removed, not redrawn.** To move
  one, remove it and draw the new line.
- **No traffic simulation.** No congestion, demand or signal timing:
  travel times come from speed limits (or default speeds per road type).
- **Turn restrictions are for routers.** Existing ones and the ones you
  draw are written to the OSM file and Valhalla obeys them; the
  GeoPackage (QGIS's own network tools) can't show them. Restrictions
  through a stretch of street ("via way") can't be drawn yet.
- **The GeoPackage is simpler than a router.** Its `car`, `bike` and
  `walk` columns say who may use each street and in which direction,
  which is what QGIS's network tools need. It knows nothing of turn
  restrictions, turn delays, bollards or ferries. For those, route on the
  PBF with a router such as Valhalla.

## Install

From a release (see [Releases](https://github.com/Yibbzz/networkforge/releases)):

```bash
pip install "networkforge @ git+https://github.com/Yibbzz/networkforge@v0.11.1"
```

(Use the newest tag on the Releases page.)

For development, clone the repo and run `uv sync`.

## Command line

```bash
# Check a custom layer's tags (seconds, no download)
networkforge check --custom proposed.gpkg --preset primary_road

# Build: every custom line a 40 mph primary road; routers get PBF,
# QGIS gets before/after GeoPackages
networkforge build --extent extent.gpkg --custom proposed.gpkg \
    --preset primary_road --tag maxspeed="40 mph" \
    --out after.osm.pbf --baseline-out before.osm.pbf \
    --gpkg after.gpkg --baseline-gpkg before.gpkg

# A network from your own lines alone (no OpenStreetMap, no area needed)
networkforge build --no-osm --custom streets.gpkg --out network.osm.pbf \
    --gpkg network.gpkg

# Area given as W,S,E,N, OSM data from a local extract
networkforge build --bbox -4.60,54.10,-4.40,54.25 --custom proposed.gpkg \
    --osm-source isle-of-man.osm.pbf --out network.osm.pbf

networkforge presets              # list presets and who can use them
networkforge info                 # version, presets, valid tag values, limits
networkforge build --help         # every option
```

**For programs driving the CLI** (e.g. a QGIS plugin):

- `--json` puts one JSON object per line on stdout; readable logs stay on
  stderr. Events: `progress`, `warning` (with the `features` or `fields`
  it's about), `done` (output paths and counts), `error` (with `type`,
  `message`, `guide`, and `issues`: `[{"feature": 3, "message": ...}]`).
- `--id-field NAME` names features in warnings and issues by that
  attribute, e.g. `--id-field fid` for a GeoPackage's feature ids, so a
  front end can select the features concerned. Default: row number.
- `networkforge info --json` describes the engine: presets, network
  types, valid tag values (for building input forms), limits.
- Exit codes: `0` success, `2` bad usage, `3` unusable input, `4` OSM
  download failed, `5` structural check failed, `1` anything else.

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
and Valhalla read. The existing network is written as OpenStreetMap
has it: every way keeps its id, its nodes and all its tags, and turn
restrictions are kept, so a router treats the "before" file like
OpenStreetMap itself. Your lines are added as new ways (tagged
`nf:custom=yes`), and where one joins an existing street the junction is
added to that street.

The PBF is tested in [Valhalla](https://valhalla.github.io/valhalla/):
one-way streets, footpaths, low bridges, unpaved roads, bus-only roads,
bridges over roads and more are each routed on and checked. See
[docs/network-analyst.md](docs/network-analyst.md), which compares the
result with ArcGIS Network Analyst's network dataset feature by feature.

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
| `tests/qgis/` | the GeoPackage routed with QGIS's own tools (`qgis_process`) | ~1 min, needs QGIS |

CI runs lint, the offline suite and the QGIS check (in the official
`qgis/qgis` Docker image, LTR and latest) on every push and pull request,
and the live tests plus a 500-example property run nightly.

To run the QGIS check locally (with QGIS installed):

```bash
uv run python -m tests.qgis.make_fixture qgis-fixture
QT_QPA_PLATFORM=offscreen python3 tests/qgis/check_with_qgis.py qgis-fixture   # QGIS's Python
```

## Releasing

1. Update `version` in `pyproject.toml` and add a `## [x.y.z]` section to
   [CHANGELOG.md](CHANGELOG.md).
2. Commit, then tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The release workflow checks the tag matches the version, runs the tests,
builds the package and publishes a GitHub Release with the changelog
notes and the wheel attached.
