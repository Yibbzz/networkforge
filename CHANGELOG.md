# Changelog

All notable changes to NetworkForge. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/) (while below 1.0, a
minor version may include breaking changes).

## [Unreleased]

### Added
- **Removing existing streets.** A feature with an OSM way id and
  `remove=yes` takes the stretch it lies along out of the "after" network:
  it is in neither the GeoPackage nor the OSM file, new lines don't join
  it, and a turn restriction that needs it goes with it. `--json`: the
  `done` event has `removed_edges`; `info` has `remove_field`.
- **Ferries.** Ferry and shuttle-train routes (`route=ferry`,
  `route=shuttle_train`, ways with no `highway` tag) are written to the OSM
  file as they are in OpenStreetMap, so routers can use them. They are not
  part of the edge table or the GeoPackage. On the Monaco extract this was
  the whole difference between walking routes on the "before" file and on
  raw OpenStreetMap; they are now identical.

### Fixed
- New junction nodes get ids above every node id in the area's OSM data,
  including nodes that are only on ferries or on removed streets.

## [0.7.0] - 2026-10-02

### Added
- **Changing existing streets.** A custom feature with an OSM way id
  (an `osm_id` attribute, as on QuickOSM layers, or `osmid`, as in
  NetworkForge's GeoPackage) changes that street instead of adding a
  line: make it one-way, close it, change its speed limit or type. Only
  attributes that differ from OpenStreetMap are applied, and only on the
  stretches the feature lies along. See "Changing existing streets" in
  the tagging guide.
- Changed stretches are marked `modified` = `yes` in the GeoPackage and
  `nf:modified=yes` in the OSM file; the "before" outputs are untouched.
- `--json`: the build's `done` event has `modified_edges`; `check`'s has
  `edits`; `info` has `edit_id_fields`. Errors about such features use
  the guide anchor `changing-existing-streets`.
- A build may consist of changes only, with no new lines.

### Changed
- A turn restriction whose way is written in several pieces (cropped at
  the edge of the area, or partly changed) now follows the piece its via
  node is on, instead of being dropped.
- `nf_edit` and `modified` join the attribute names set aside as
  internal. Such names are now only noted, not warned about, when the
  layer has OSM ids (rows copied from a NetworkForge layer always have
  them).

## [0.6.0] - 2026-10-02

### Added
- Valhalla tests (`tests/valhalla`, `uv run pytest -m valhalla`, CI job
  "PBF in Valhalla"): the exported PBF is built into a Valhalla routing
  graph with a pinned `pyvalhalla` and routed on. They cover every
  feature of Esri's "Create a network dataset" tutorial, who Valhalla
  lets onto each kind of custom line, every way a line can join the
  network, and that the "before" file routes exactly like the OSM data.
- `docs/network-analyst.md`: each ArcGIS Network Analyst feature, the
  tag that replaces it, the test that proves it, and what isn't covered.
- A custom line that crosses itself gets a junction at the crossing
  (unless it is a bridge or tunnel).

### Changed
- OSM / PBF export writes the existing network as OpenStreetMap has it,
  instead of one new 2-node way per edge. Every way keeps its id, its
  nodes in order and all its tags (not only the tags NetworkForge lists),
  tagged nodes keep all theirs, and turn restrictions and route relations
  are kept. Where a custom line joins a street mid-way, the new junction
  node is inserted into that way. Each custom line is one way (id above
  every OSM way id). Measured with Valhalla on a Monaco extract: routes on
  the "before" file used to differ from routes on the OSM data itself for
  about a third of trips; car, bicycle, bus and truck routes are now
  identical.
- The `osmid` edge column (and GeoPackage field) is the OSM way id, empty
  for custom edges. It used to be a row number.
- The Overpass download and a local extract now share one code path, and
  one further Overpass request fetches the area's turn restrictions.
- More attributes of custom features are written as tags: time-dependent
  access (`access:conditional`, ...), `taxi`, `hov`, `moped`, `emergency`,
  `motorroad`, `maxaxles`, `hazmat`, `turn:lanes`, `destination`,
  `alt_name`, `sidewalk` and others (see `networkforge info`).

- Access rules now follow OSM's defaults where Valhalla showed they
  didn't: cycling on `highway=pedestrian` needs a tag that allows it
  (the `pedestrian_street` preset now sets `bicycle=yes`, so it still
  allows cycling), and `motorroad=yes` closes a road to walking and
  cycling.
- A custom line only buses or other special vehicles may use (a busway,
  `access=no` + `bus=yes`) is accepted with a note instead of rejected as
  "unusable by every mode".
- `bus`, `psv`, `hgv`, `goods`, `taxi`, `motorcycle`, `moped`,
  `emergency`, `horse`, `hov` are checked like the other access tags, and
  `maxheight`, `maxwidth`, `maxlength`, `maxweight`, `maxaxleload` must be
  valid OSM limits (`networkforge info`: `tag_values`, `tag_patterns`).
- GeoPackage `bike_direction` treats `cycleway=opposite*` as contraflow
  cycling, like `oneway:bicycle=no`.

### Fixed
- A two-way street was written to the OSM / PBF file twice, once per
  direction, which routers read as two parallel streets.
- When a custom line ended on an existing junction, every street at that
  junction gained a second, uncut copy of itself, which bypassed any new
  junction on that street.

## [0.5.0] - 2026-10-01

### Changed
- Custom lines that don't connect to the rest of the network no longer
  fail the build (`NetworkIntegrityError`, exit code 5). The build
  finishes with a warning - a `--json` `warning` event - naming the
  `features`; they are kept in the output. A build where no line
  reaches the network still fails (`NoIntersectionError`).

### Added
- `validation.disconnected_custom_edges(edges)`: which custom edges a
  router can't reach.

## [0.4.0] - 2026-09-30

### Added
- `networkforge build --baseline-gpkg`: the untouched OSM network as a
  GeoPackage with the same analysis columns, for before/after in QGIS.
- `--id-field NAME` (build, check): name features in warnings and errors
  by an attribute, e.g. a GeoPackage's `fid`, instead of row number.
- Errors carry `issues` - `[{"feature": id, "message": ...}]` - on the
  exception (`NetworkForgeError.issues`) and in `--json` error events.
- `--json` emits `warning` events, with the `features` / `fields`
  they're about.
- `networkforge info [--json]`: version, presets, network types, valid tag
  values and limits, for front ends building their UI from the engine.

### Changed
- Custom attributes with internal names (`length`, `key`, `u`, `v`, ...)
  are set aside with a warning instead of failing the build.
- Custom feature ids (the data's index) must be unique.

## [0.3.0] - 2026-09-29

### Added
- GeoPackage output for QGIS (`write_gpkg`, `networkforge build --gpkg`):
  edges carry network-analysis columns (`car`/`bike`/`walk`,
  `speed_kph`, `length_m`, travel minutes per mode, `car_direction` /
  `bike_direction` for QGIS's direction field, incl. contraflow cycling).
  One row per street (OSMnx's reverse copies of two-way streets are
  dropped) and every edge ends exactly on its nodes.
- CI job routing on the GeoPackage with QGIS's own network analysis
  (`qgis_process`, QGIS LTR and latest) and checking the routes.

### Removed
- `scripts/` (`get_test_data.py`, `route_test.py`,
  `diagnose_custom_network.py`): replaced by the CLI (`networkforge build`,
  `networkforge check`) and the test suite.

### Fixed
- The dev container's Dockerfile installs dependencies only
  (`uv sync --no-install-project`); installing the package itself failed
  at image build time because the source isn't in the image.

## [0.2.0] - 2026-09-28

### Added
- **Command-line interface**: `networkforge build`, `check` and `presets`
  (also `python -m networkforge`). `--json` writes progress, results and
  errors as JSON lines for programs; documented exit codes.
- **Local OSM extracts**: `build_network(..., osm_source="area.osm.pbf")`
  reads the existing network from a file (e.g. Geofabrik) instead of
  Overpass, cropped and filtered to match the Overpass result exactly.
- **PBF export**: `write_osm()` picks the format from the file name:
  `.osm`, `.osm.pbf`, `.pbf`, `.osm.gz`, `.osm.bz2`.
- **Presets** (`preset="primary_road"`, ...) and per-feature tags:
  each feature's own attributes, with blanket tags filling gaps, or
  replacing values with `overwrite_tags=True`.
- **Tag validation** before any download: OSM value checks, which modes
  can use each feature, and warnings for misspelt or Shapefile-truncated
  attribute names.
- **Grade separation**: custom lines don't join motorways, bridges,
  tunnels or other layers they cross, but may end on them (slip roads).
- **Transport-mode access rules** following the OSM access hierarchy
  (`motorcar` > `motor_vehicle` > `vehicle` > `access`; `bicycle`,
  `foot`), busways, and shared-use paths.
- **Exception classes** (`NetworkForgeError`, `InputError`,
  `InvalidTagsError`, `NoIntersectionError`, `OSMDownloadError`,
  `NetworkIntegrityError`) with plain-language messages linking to the docs.
- **Input checks**: CRS, geometry types, bounding box coverage, Z values.
- Progress reporting via `logging` and a `progress` callback.
- Docs: `docs/tagging-guide.md`, `docs/osm-data.md`.
- Tests: unit, synthetic-grid integration, Hypothesis property tests and
  live city tests; CI with coverage.

### Changed
- `network_tags` is optional; `build_network` has 13 steps (the old
  final "apply tags" step is folded into the first).
- Boxes over 1,000 km² are refused for Overpass; use `osm_source`.
- Exports keep routing-relevant way tags (one-way exceptions, cycle
  infrastructure, vehicle limits, surface) and node tags (barriers,
  signals, crossings).
- Much faster: splitting, node assignment and export are vectorised
  (demo area: build excluding download 23 s -> 5 s; 800 custom lines
  191 s -> 52 s). 

### Fixed
- Custom node ids could collide with real OSM node ids, moving streets.
- Snapping could bend or rewire existing streets near custom lines
  (found by property tests).
- Custom lines could get gaps where segments ended near a node.
- `oneway` was exported as `True`/`False`, which made one-way streets
  two-way when reloaded; now `yes`/`no`.
- Access restrictions (`motor_vehicle`, `foot`, `bicycle`, ...) were
  dropped on download and export.
- Baseline and custom travel times weren't comparable: speeds for
  untagged roads now come from a fixed table.

## [0.1.0]

- Initial pipeline: merge custom lines into an OSMnx network and export
  OSM XML.

[Unreleased]: https://github.com/Yibbzz/networkforge/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/Yibbzz/networkforge/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Yibbzz/networkforge/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Yibbzz/networkforge/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Yibbzz/networkforge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Yibbzz/networkforge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Yibbzz/networkforge/releases/tag/v0.2.0
