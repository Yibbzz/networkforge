# NetworkForge

[![tests](https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml/badge.svg)](https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml)
[![coverage](https://codecov.io/gh/Yibbzz/networkforge/graph/badge.svg)](https://codecov.io/gh/Yibbzz/networkforge)

Integrate custom (proposed) roads, cycleways and paths into an existing
OpenStreetMap network, following OSM access rules, so the result can be
routed for any transport mode and compared with the original network.

## Setup

```bash
uv sync
```

## Usage

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

## Scripts

- `scripts/get_test_data.py`: build the demo network in `tests/data`
- `scripts/route_test.py`: compare routes with and without the custom network
- `scripts/diagnose_custom_network.py`: report on how a custom network was integrated
