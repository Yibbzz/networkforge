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
import geopandas as gpd
from networkforge.network import build_network
from networkforge.export import write_osm_xml

bbox = gpd.read_file("extent.geojson")
custom = gpd.read_file("proposed.geojson")  # per-feature tags: highway, maxspeed, ...

nodes, edges = build_network(bbox, custom, network_tags={"highway": "primary"})
write_osm_xml(nodes, edges, "network.osm")
```

Tags on each custom feature override `network_tags`. Invalid or
unusable tags stop the build before anything is downloaded
(`strict=False` turns that into warnings).

Custom lines join every street they cross, except where either side
is grade-separated: motorways, motorway slip roads, bridges, tunnels,
or a different `layer`. Tag a custom feature `bridge=yes` (or give it
a `layer`) to take it over the streets below. A line that *ends* on a
motorway still joins it, so new slip roads work.

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
