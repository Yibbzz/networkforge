# Changelog

All notable changes to NetworkForge. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/) (while below 1.0, a
minor version may include breaking changes).

## [Unreleased]

### Added
- GeoPackage output for QGIS (`write_gpkg`, `networkforge build --gpkg`):
  edges carry network-analysis columns (`car`/`bike`/`walk`,
  `speed_kph`, `length_m`, travel minutes per mode, `car_direction` /
  `bike_direction` for QGIS's direction field, incl. contraflow cycling).
  One row per street (OSMnx's reverse copies of two-way streets are
  dropped) and every edge ends exactly on its nodes.
- CI job routing on the GeoPackage with QGIS's own network analysis
  (`qgis_process`, QGIS LTR and latest) and checking the routes.

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

[Unreleased]: https://github.com/Yibbzz/networkforge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Yibbzz/networkforge/releases/tag/v0.2.0
