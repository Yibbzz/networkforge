# NetworkForge

Goal: merge user-supplied custom road/path geometry (e.g. proposed infrastructure) into an
existing OpenStreetMap network so routing calcs (OSMnx now; GraphHopper/Valhalla later via
OSM XML/PBF) can compare "before" vs "after". See `proposed_arc.txt` for the target architecture.

Python >= 3.12, managed with `uv`. Run things with `uv run python ...` / `uv run pytest`.

Releases: bump `version` in pyproject.toml + CHANGELOG.md section, then push tag `vX.Y.Z`;
`.github/workflows/release.yml` checks, tests, builds and publishes a GitHub Release.
No QGIS code in this repo - a plugin will live in a separate repo and call the CLI/API.

Tests: `uv run pytest` runs the offline suite (~30 s). `uv run pytest -m network` runs the live
tests (download OSM; ~5 min). Offline is the default via `addopts` in pyproject.toml.
Lint: `uv run ruff check .`. Coverage: `uv run pytest --cov`.
The package is installed editable by `uv sync` (hatchling build-system), no PYTHONPATH needed.

## Source (`src/networkforge/`)
- `cli.py` - `networkforge build|check|presets` (console script; also `python -m networkforge`).
  `--json` = JSON lines on stdout (progress/done/error events) for programs such as a future QGIS
  plugin; human logs on stderr. Exit codes 0/1/2/3 input/4 download/5 integrity. Keep this and the
  JSON event shapes stable: other tools depend on them.
- `network.py` - `build_network()`: the 13-step pipeline (check inputs/tags -> download OSM -> snap
  custom lines -> find junctions -> split -> merge nodes -> assign u/v -> validate). Logs via
  `logging` (never print in src/) and calls optional `progress(step, total, text)`.
  Tags: feature attributes + blanket (`preset` then `network_tags`), `overwrite_tags` flips priority.
- `presets.py` - named tag sets (`PRESETS`); no maxspeed on purpose. Every preset must appear in
  the docs/tagging-guide.md table with correct tags and Car/Bike/Walk ticks (test_presets.py checks).
- `inputs.py` - pre-build checks: bbox/custom GeoDataFrames, CRS, lines only, inside bbox, 2D.
  Boxes over `MAX_OVERPASS_AREA_KM2` (1,000) are refused unless `osm_source` is given.
- `errors.py` - exception hierarchy (NetworkForgeError; InputError is also a ValueError). Raise these,
  not bare ValueError/AssertionError; pass `guide=` anchor into docs/tagging-guide.md where useful.
- `topology.py` - geometry ops used by the pipeline: intersection points, splitting at buffered
  points, node dedup (`snap_tolerance`, metres), nearest-node u/v assignment (0.1 m), u/v consistency.
- `osm.py` - existing network from Overpass (`get_osm_data_from_bbox`, OSMnx cache in
  `NETWORKFORGE_OSMNX_CACHE`) or a local extract (`get_osm_data_from_file`, via `osm_source=`):
  pyosmium copies filtered ways/nodes in the 500 m-buffered bbox to temp XML, then the same steps as
  ox.graph_from_polygon (truncate, largest component, street_count). Must match Overpass exactly
  (tests/live/test_local_extract.py). Ways cut at an extract's edge keep their known runs.
  `network_type` = OSMnx download filter; keep `"all"` so one build serves every mode.
- `modes.py` - transport-mode access rules: OSMnx's own network filters (by way type) + the OSM access
  hierarchy (`ACCESS_HIERARCHY`, most specific tag wins; `DENIED_ACCESS`; busways closed unless a
  mode tag opens them; `OPENABLE` e.g. footway+bicycle=designated). OSMnx's own access clauses are
  skipped for routing modes (the hierarchy replaces them). `usable_modes(tags)`,
  `filter_graph_by_mode`, `load_graph(osm_file, mode)`, `keep_mode_tags()` (way + node tags to keep).
- `tags.py` - which way/node tags are kept at download and written on export (single source).
- `projection.py` - picks a projected UTM analysis CRS from the bbox; WGS84 helpers.
- `validation.py` - pre-build `resolve_custom_tags` (feature attributes vs blanket tags, `overwrite`)
  + `check_custom_tags` (also warns on misspelt/truncated attribute names) (valid OSM tag values, usable by >= 1 mode / by the
  chosen network_type; raises when strict). Post-build structural invariants: valid u/v nodes, no
  self-loops, custom edges connected, custom lines unbroken.
- Outputs have two audiences: OSM/PBF (`write_osm`) for routers (Valhalla, GraphHopper), which read
  tags themselves; GeoPackage (`write_gpkg` -> `analysis_edges`) for QGIS network analysis, with
  car/bike/walk flags (same `allows_mode` rules, car = "drive"), speed_kph (`modes.car_speed_kph`),
  length_m, *_minutes, car/bike_direction (forward/backward/both). One row per street (drops OSMnx
  reverse copies where reversed & two-way); geometry rebuilt u->v so QGIS topology is exact.
  tests/integration/test_gpkg.py routes on the layer "as QGIS does" and must match the engine.
- `export.py` - `write_osm(nodes, edges, path)`: format from extension (.osm via ElementTree; .osm.pbf,
  .pbf, .osm.gz, .osm.bz2 via pyosmium). Both from `prepare_osm_data()` so content is identical; sorted
  by id. Every edge becomes a 2-node way; custom edges get `nf:custom=yes`. `write_osm_xml` = XML only.

## Tests (`tests/`) and scripts (`scripts/`)
- `unit/` - tag/mode rules (`test_custom_tags.py`), CRS selection (`test_projection.py`).
- `integration/` - offline. `grid.py` is a hand-built 5x5 OSM grid (footway, cycleway, bus gate,
  motorway, small node ids); `conftest.py` monkeypatches `ox.graph_from_bbox` to return it
  (`fake_osm`, `build` fixtures). `test_synthetic_grid.py` = exact rule checks + regression cases
  Hypothesis found; `test_properties.py` = Hypothesis random custom lines, checks invariants.
  - `live/` - `network` marker: `test_cities.py` (3 cities x road/cycleway/footway, seeded via
  `NF_TEST_SEED`, invariants only) and `test_structural_invariants.py` (full demo extent).
- `helpers.py` - build/export/load/route helpers shared by integration and live tests.
- `conftest.py` - Hypothesis profiles via `HYPOTHESIS_PROFILE`: dev (20), ci (50), thorough (500).
- `tests/data/` - demo inputs; the .osm/.gpkg files are generated by `scripts/get_test_data.py`.
- `scripts/` - `get_test_data.py`, `route_test.py` (MODE, before/after routing),
  `diagnose_custom_network.py` (offline report on an exported .osm). Run from the repo root.
- CI: `.github/workflows/tests.yml` - ruff + offline tests with coverage (`fail_under` in
  pyproject) on push/PR; live tests + thorough Hypothesis nightly.

Docs: `docs/tagging-guide.md` is the user-facing reference (presets, attributes, recipes, errors);
`docs/osm-data.md` covers Overpass vs local extracts. Error `guide=` is a tagging-guide anchor or
"file.md#anchor" in docs/.
Keep it in sync with presets.py/validation.py when tags or messages change.

## Gotchas
- OSMnx drops way tags not in `ox.settings.useful_tags_way`; add `nf:custom` before `graph_from_xml`.
- Unitless `maxspeed` is km/h. UK data usually wants `"50 mph"`.
- Design: build once with every mode (`network_type="all"`), filter by mode at routing time. Loading
  an .osm with plain `ox.graph_from_xml` ignores access rules (cars on cycleways) - use `load_graph`.
- OSMnx travel_time uses car speeds; for bike/walk route by length or set your own speeds.
- Use `modes.add_travel_times` (fixed `DEFAULT_SPEEDS_KPH`), not bare `ox.add_edge_speeds`: OSMnx's
  default imputes per-type mean maxspeed across the graph, so custom edges shift OSM edge speeds.
- New nodes get ids above the max OSM id (topology.py). Never renumber from 0: collides with real OSM ids.
- Snapping happens BEFORE splitting (`snap_line_vertices_to_network`, `snap_points_to_network`):
  within `snap_tolerance`, onto the nearest OSM node, else onto the nearest OSM edge. OSM lines are
  only split at points exactly on them; only custom lines bend to meet a junction. Splitting OSM
  streets at nearby-but-off-line points bent them and created shortcuts (found by Hypothesis).
- Grade separation (topology.py): crossings only become junctions if `crosses_at_grade` - neither
  way is motorway/motorway_link/bridge/tunnel and `layer` matches (applies to custom features too, e.g.
  bridge=yes). A custom line's END points may still join a grade-separated way (slip roads); middle
  vertices only snap to `joinable_network`. Trunk is deliberately treated as at-grade.
- OSMnx can't read PBF: tests convert PBF -> XML with pyosmium before `load_graph`.
- `oneway:bicycle` is exported, but OSMnx-based routing (`load_graph`) doesn't apply it.
- Performance: no per-row Python loops (iterrows/apply/.loc per row) over the OSM network - vectorise
  with shapely/numpy/pandas. Split (`_split_at_points`), node assignment (`nearest_point_ids`, STRtree)
  and export (`_row_tags`, streamed XML) are vectorised; `create_points_from_gdf` (step 5) still loops
  over custom lines. The OSMnx download/graph build (~35 s for the demo area) dominates.
- Export writes booleans as yes/no: OSMnx turns oneway into True/False, and "True" is read back as
  two-way (OSMnx's oneway check is case-sensitive).
