# NetworkForge

Goal: merge user-supplied custom road/path geometry (e.g. proposed infrastructure) into an
existing OpenStreetMap network so routing engines (Valhalla/GraphHopper via OSM PBF) and QGIS
(via GeoPackage) can compare "before" vs "after". Planned: a separate QGIS plugin repo, later a
web app with hosted Valhalla - both use this engine via the CLI/API; no QGIS code here.

Python >= 3.12, managed with `uv`. Run things with `uv run python ...` / `uv run pytest`.

Releases: bump `version` in pyproject.toml + CHANGELOG.md section, then push tag `vX.Y.Z`;
`.github/workflows/release.yml` checks, tests, builds and publishes a GitHub Release.
No QGIS code in this repo - a plugin will live in a separate repo and call the CLI/API.

Tests: `uv run pytest` runs the offline suite (~1 min). `uv run pytest -m valhalla` routes on the
exported PBF with Valhalla (~3 min). `uv run pytest -m network` runs the live tests (download OSM;
~5 min). Fast + offline is the default via `addopts` in pyproject.toml, so `-m valhalla` is needed
even when naming a file in tests/valhalla.

Goal (2026-10-02): match ArcGIS Network Analyst's "Create a network dataset" with OSM tags +
Valhalla (run by routing.earth's QGIS Network Analyst plugin from our PBF). "The PBF routes
correctly in Valhalla" is the output that matters most. docs/network-analyst.md maps each Esri
feature to its tag and its test, and lists what is not covered; keep it in sync.
Lint: `uv run ruff check .`. Coverage: `uv run pytest --cov`.
The package is installed editable by `uv sync` (hatchling build-system), no PYTHONPATH needed.

## Source (`src/networkforge/`)
- `cli.py` - `networkforge build|check|presets|info` (console script; also `python -m networkforge`).
  `--json` = JSON lines on stdout (progress/warning/done/error events; errors carry `issues`) for
  programs such as the QGIS plugin; human logs always on stderr. `--id-field` sets the custom data's
  index, which names features everywhere (`NetworkForgeError.issues`, `extra={"features": ...}` on
  log warnings -> JSON warning events via `_WarningEvents`). `--baseline-gpkg` = before network.
  Exit codes 0/1/2/3 input/4 download/5 integrity. Keep flags and JSON shapes stable: the plugin
  depends on them (breaking changes = new engine version).
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
- `edits.py` - changing existing streets: custom features with an OSM way id (`osm_id` /
  `osmid`, `EDIT_ID_COLUMNS`) are split off in step 1 (`split_edits`) and applied to a COPY of the
  OSM edge table right after download (`apply_edits`); the before network is never touched. Only
  tags differing from the source way's tags are changes; a feature claims the edges of that way
  whose midpoint is within snap_tolerance (nearest feature wins; equal claims must agree). `oneway`
  is relative to the feature's drawn direction and stored in way terms; one-way changes rebuild
  the stretch's edges OSMnx-style (`_in_direction`). Edges get `modified="yes"` + `nf_edit` (edit
  number); tag changes travel in `edges.attrs[EDITS_ATTR]`. Export writes each edited run as its
  own way (`nf:modified=yes`), the way id staying with the first unchanged run; `_relations` remaps
  restriction members to the piece holding the via node. Edits-only builds return after step 2.
  `remove=yes` (`REMOVE_COLUMN`) on such a feature drops the edges instead; the nodes only they
  used leave `nodes_gdf` (so new lines can't snap to them) but stay in the before nodes; export
  needs nothing special (a missing stretch splits the way, like cropping).
- `topology.py` - geometry ops used by the pipeline: intersection points, splitting at buffered
  points, node dedup (`snap_tolerance`, metres), nearest-node u/v assignment (0.1 m), u/v consistency.
- `osm.py` - existing network from Overpass (`get_osm_data_from_bbox`, OSMnx cache in
  `NETWORKFORGE_OSMNX_CACHE`) or a local extract (`get_osm_data_from_file`, via `osm_source=`).
  Both produce Overpass-JSON "elements" (`_download_elements` / `_read_elements`), then one path
  (`_network_from_elements`): OSMnx's private `_create_graph` + the same steps as
  ox.graph_from_polygon with retain_all (truncate, street_count; EVERY piece of network is kept -
  "largest component only" dropped Gozo and 89% of the Maldives and shifted Valhalla's speeds). Both attach an `OSMSource`
  (every way's nodes + ALL tags, tagged nodes, turn-restriction / route relations) as
  `edges.attrs[SOURCE_ATTR]`; build_network re-attaches it to its results and export writes the
  existing network from it. Ways cut at an extract's edge keep their known runs.
  `network_type` = OSMnx download filter; keep `"all"` so one build serves every mode.
- `modes.py` - transport-mode access rules: OSMnx's own network filters (by way type) + the OSM access
  hierarchy (`ACCESS_HIERARCHY`, most specific tag wins; `DENIED_ACCESS`; busways closed unless a
  mode tag opens them; `OPENABLE` e.g. footway+bicycle=designated). OSMnx's own access clauses are
  skipped for routing modes (the hierarchy replaces them). `usable_modes(tags)`,
  `filter_graph_by_mode`, `load_graph(osm_file, mode)`, `keep_mode_tags()` (way + node tags to keep).
- `tags.py` - tags written for CUSTOM features and kept as edge/node columns (single source);
  `ROUTING_NODE_KEYS` (which extract nodes are read), relation types kept, `PART_COLUMN`.
- `projection.py` - picks a projected UTM analysis CRS from the bbox; WGS84 helpers.
- `validation.py` - pre-build `resolve_custom_tags` (feature attributes vs blanket tags, `overwrite`)
  + `check_custom_tags` (also warns on misspelt/truncated attribute names) (valid OSM tag values, usable by >= 1 mode / by the
  chosen network_type; raises when strict). Post-build structural invariants: valid u/v nodes, no
  self-loops, custom lines unbroken. Custom lines not connected to the network only WARN in the
  build (naming features; kept in the output) - tests assert `assert_all_custom_edges_are_connected`.
- Outputs have two audiences: OSM/PBF (`write_osm`) for routers (Valhalla, GraphHopper), which read
  tags themselves; GeoPackage (`write_gpkg` -> `analysis_edges`) for QGIS network analysis, with
  car/bike/walk flags (same `allows_mode` rules, car = "drive"), speed_kph (`modes.car_speed_kph`),
  length_m, *_minutes, car/bike_direction (forward/backward/both). One row per street (drops OSMnx
  reverse copies where reversed & two-way); geometry rebuilt u->v so QGIS topology is exact.
  tests/integration/test_gpkg.py routes on the layer "as QGIS does" and must match the engine.
- `export.py` - `write_osm(nodes, edges, path)`: format from extension (.osm via streamed XML;
  .osm.pbf, .pbf, .osm.gz, .osm.bz2 via pyosmium). Both from `prepare_osm_data()` so content is
  identical; sorted by id. Existing ways are written from the `OSMSource`: same id, node order and
  tags; ways the build cut or the bbox cropped are rebuilt around their OSM nodes (`_runs`: new
  junction nodes inserted in place). Relations kept if their members survive. Each custom line is
  ONE way (pieces chained by `nf_part`, `_chains`), id above the max way id, `nf:custom=yes`.
  Edge tables without a source (straight from OSMnx) fall back to chaining by `osmid` + column
  tags. Never go back to one 2-node way per edge or to writing OSMnx's reverse copies: Valhalla
  routes measurably differently (tests/valhalla).

## Tests (`tests/`)
- `unit/` - tag/mode rules (`test_custom_tags.py`), CRS selection (`test_projection.py`).
- `integration/` - offline. `grid.py` is a hand-built 5x5 OSM grid (footway, cycleway, bus gate,
  motorway + slip road, small node ids, multi-block named ways, one turn restriction) as Overpass
  elements (`grid_elements()`); `conftest.py` monkeypatches `osm._download_elements` to return it
  (`fake_osm`, `build` fixtures). `test_synthetic_grid.py` = exact rule checks + regression cases
  Hypothesis found; `test_properties.py` = Hypothesis random custom lines, checks invariants.
  - `live/` - `network` marker: `test_cities.py` (3 cities x road/cycleway/footway, seeded via
  `NF_TEST_SEED`, invariants only) and `test_structural_invariants.py` (full demo extent).
- `helpers.py` - build/export/load/route helpers shared by integration and live tests.
- `qgis/` - `make_fixture.py` (project env: grid GeoPackage + expected routes via
  `helpers.qgis_graph`) and `check_with_qgis.py` (QGIS's Python, stdlib only: runs
  native:shortestpathpointtopoint / serviceareafrompoint via `qgis_process`). qgis_process needs
  `--PROJECT_PATH` (an empty .qgs) BEFORE the `--` separator. Locally, run it with a clean env
  (`env -i PATH=/usr/bin:/bin ...`): this container's PYTHONPATH/venv break QGIS's Python.
  CI job `qgis` runs it in the qgis/qgis Docker image (ltr + latest).
- `valhalla/` - `valhalla` marker; pyvalhalla is pinned (==) in the dev group: Valhalla's rules
  change between versions, upgrade deliberately. `harness.py` = `Router.from_pbf()` (runs the wheel's
  `valhalla_build_tiles`, ~1 s), `.route()` -> length/time/way ids/names, `.matrix()`, `.edges(way)`
  (how Valhalla read a way, via /locate). `conftest.py`: session `scenario(features, **kwargs)` builds
  before/after on the grid (cached), `at(col, row)` / `xy(col, row)` grid positions.
  `test_esri_network_dataset.py` (one test per Esri tutorial feature), `test_tags_as_valhalla_reads_them.py`
  (Valhalla access vs `usable_modes`; every disagreement must be listed in `DIFFERENCES`),
  `test_joins.py` (topology edge cases), `test_before_and_after.py` (before == raw OSM exactly;
  Valhalla == our OSMnx routing), `test_random_lines.py` (Hypothesis). Writing these tests: put the
  line mid-route (Valhalla skips restrictions on the first/last edge), use `shortest=True` (default
  costing prefers main roads / U-turns at dead ends), avoid equal-length alternatives on the grid.
  `test_edits.py` (edited existing streets). Turn-restriction checks use `turn_metres()` (a
  tie-free trip across the grid's restricted turn: 100 m allowed, 300 m not).
  Live: `tests/live/test_valhalla_real_data.py` (Monaco extract: vehicles identical to raw OSM;
  closing a street via rows copied from the before GeoPackage).
- `conftest.py` - Hypothesis profiles via `HYPOTHESIS_PROFILE`: dev (20), ci (50), thorough (500).
- `tests/data/` - demo inputs (extent + custom lines, used by test_structural_invariants). Build the
  demo network with the CLI: `networkforge build --extent tests/data/extent.geojson --custom
  tests/data/custom_road_test.geojson --preset primary_road --out ... --baseline-out ...`.
- `tests/commands.txt` - the user's own notes; leave it alone.
- CI: `.github/workflows/tests.yml` - ruff + offline tests with coverage (`fail_under` in
  pyproject), the Valhalla job and the QGIS check on push/PR/nightly; live tests + thorough
  Hypothesis nightly.

Docs: `docs/tagging-guide.md` is the user-facing reference (presets, attributes, recipes, errors);
`docs/osm-data.md` covers Overpass vs local extracts. Error `guide=` is a tagging-guide anchor or
"file.md#anchor" in docs/.
Keep it in sync with presets.py/validation.py when tags or messages change.

## Gotchas
- Overpass path makes two requests: OSMnx's own ways+nodes query, then relations (`rel(bw.w)`) for
  restrictions/routes. `overpass-api.de` sometimes refuses connections from this container.
- A piece left without two end nodes after splitting (zero-length offcut where a line is cut at
  its own end) must be dropped (network.py step 10): it kept the parent's u/v and became a second,
  uncut copy of the street.
- A custom line crossing itself is cut at the crossing first (`split_at_self_crossings`) so it gets
  a junction there; grade-separated lines are left whole.
- modes.py vs Valhalla: bikes on `highway=pedestrian` need a bicycle tag; `motorroad=yes` closes
  walk/bike; lines open only to buses etc. (`open_to_other_vehicles`) are valid, with a note.
- GeoPackage / OSMnx routing ignores node barriers and turn restrictions; Valhalla obeys both.
- Junction points carry what they may join (`POINT_COLUMNS`: is_end, separated, layers). An OSM
  line is only split at a point on it if the point is a custom line's END or the line is at grade
  on that layer; and new points merge only with nodes they sit exactly ON (`ON_NODE_TOLERANCE`),
  never by distance - both let a path near a tunnel/bridge node weld the tunnel to the street.
- "A custom line is stranded" = its piece of network has no existing street (not "outside the
  largest piece": the OSM network itself can be several pieces).
- Attribute values: booleans -> yes/no, empty text -> no value (`validation._tag_value`).
- New way ids start above every way id in the area's source; pieces of cropped ways are numbered
  first, in a fixed order, so they match between the before and after file.
- Valhalla's matrix is not exact: on big graphs a few cells differ from `route()` for the same
  pair, and tiny density differences flip ties. Compare with `route()` before calling it a bug.
- Valhalla 3.9 routes only on `highway=*` ways plus `route=ferry|shuttle_train`; NOT on piers,
  platforms or `area=yes` squares (tested), so OSMnx's "all" filter loses nothing but ferries.
  Ferries (`osm.is_ferry`, `OSMSource.ferries` / `ferry_nodes`, cropped to the bbox) bypass the
  edge table and are written verbatim by export; new node ids must start above ferry node ids.
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
