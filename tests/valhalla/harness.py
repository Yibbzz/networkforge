"""
Routing on NetworkForge's OSM PBF output with Valhalla (pyvalhalla),
the router the QGIS Network Analyst plugin runs.

    router = Router.from_pbf("after.osm.pbf", work_dir)
    route = router.route(start, end, "auto")          # None if there is no route
    route.length_m, route.time_s, route.way_ids, route.names
    router.edges(way_id)                              # how Valhalla read that way

Points are (lon, lat). Costing names are Valhalla's: auto, bus, truck,
bicycle, pedestrian, ...; keyword arguments are that costing's options
(https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/).
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import osmium
from valhalla._scripts import PYVALHALLA_BIN_DIR
from valhalla.config import get_config

import valhalla

# The version the expectations in tests/valhalla were written against
# (pinned in pyproject.toml). Valhalla's rules change between versions.
VALHALLA_VERSION = "3.9.0"

LonLat = tuple[float, float]


@dataclass(frozen=True)
class Route:
    length_m: float
    time_s: float
    way_ids: tuple[int, ...]   # OSM way ids travelled, in order, without repeats
    names: tuple[str, ...]     # street names in the directions, in order
    shape: str                 # encoded polyline (6 decimals)

    def uses(self, way_id: int) -> bool:
        return way_id in self.way_ids


class Router:
    """A Valhalla routing graph built from one OSM file."""

    def __init__(self, actor: valhalla.Actor, pbf: Path):
        self.actor = actor
        self.pbf = Path(pbf)
        self._ways = None
        self._nodes = None

    @classmethod
    def from_pbf(cls, pbf: Path, work_dir: Path) -> "Router":
        """Build Valhalla's graph tiles for `pbf` in `work_dir` (about a second)."""
        work_dir = Path(work_dir)
        tile_dir = work_dir / "tiles"
        tile_dir.mkdir(parents=True, exist_ok=True)

        config = get_config(tile_extract="", tile_dir=str(tile_dir))
        config["mjolnir"]["concurrency"] = 1
        config_path = work_dir / "valhalla.json"
        config_path.write_text(json.dumps(config))

        build = subprocess.run(
            [str(PYVALHALLA_BIN_DIR / "valhalla_build_tiles"), "-c", str(config_path), str(pbf)],
            capture_output=True, text=True, check=False,
        )
        if build.returncode != 0 or not any(tile_dir.rglob("*.gph")):
            raise RuntimeError(
                f"Valhalla could not build a graph from {pbf}:\n{build.stdout[-2000:]}"
                f"\n{build.stderr[-2000:]}"
            )
        return cls(valhalla.Actor(str(config_path)), pbf)

    # ------------------------------------------------------------ the file

    @property
    def ways(self) -> dict[int, tuple[list[int], dict[str, str]]]:
        """way id -> (node refs, tags), as written in the OSM file."""
        if self._ways is None:
            self._ways = {
                way.id: ([n.ref for n in way.nodes], dict(way.tags))
                for way in osmium.FileProcessor(str(self.pbf), osmium.osm.WAY)
            }
        return self._ways

    def custom_way_ids(self) -> list[int]:
        return [way_id for way_id, (_, tags) in self.ways.items()
                if tags.get("nf:custom") == "yes"]

    # ------------------------------------------------------------ routing

    def route(self, start: LonLat, end: LonLat, costing: str, **options) -> Route | None:
        """The route Valhalla chooses, or None if it finds none."""
        request = {
            "locations": [_location(start), _location(end)],
            "costing": costing,
            "costing_options": {costing: options},
            "units": "kilometers",
        }
        try:
            trip = self.actor.route(request)["trip"]
        except RuntimeError as exc:
            if _is_no_route(exc):
                return None
            raise

        leg = trip["legs"][0]
        names = []
        for maneuver in leg["maneuvers"]:
            for name in maneuver.get("street_names", []):
                if not names or names[-1] != name:
                    names.append(name)

        return Route(
            length_m=trip["summary"]["length"] * 1000,
            time_s=trip["summary"]["time"],
            way_ids=self._way_ids(leg["shape"], costing, options),
            names=tuple(names),
            shape=leg["shape"],
        )

    def _way_ids(self, shape: str, costing: str, options: dict) -> tuple[int, ...]:
        """The OSM ways a route shape runs along (Valhalla's map matching)."""
        matched = self.actor.trace_attributes({
            "encoded_polyline": shape,
            "shape_match": "walk_or_snap",
            "costing": costing,
            "costing_options": {costing: options},
            "filters": {"attributes": ["edge.way_id"], "action": "include"},
        })
        way_ids = []
        for edge in matched["edges"]:
            if not way_ids or way_ids[-1] != edge["way_id"]:
                way_ids.append(edge["way_id"])
        return tuple(way_ids)

    def matrix(self, points: list[LonLat], costing: str, **options) -> list[list[tuple]]:
        """[(length_m, time_s) or None] between every pair of points."""
        result = self.actor.matrix({
            "sources": [_location(p) for p in points],
            "targets": [_location(p) for p in points],
            "costing": costing,
            "costing_options": {costing: options},
            "units": "kilometers",
        })
        return [
            [None if cell["distance"] is None else (cell["distance"] * 1000, cell["time"])
             for cell in row]
            for row in result["sources_to_targets"]
        ]

    # ------------------------------------------------------------ inspecting

    def edges(self, way_id: int) -> list[dict]:
        """
        Valhalla's directed edges for an OSM way (one or two per stretch
        between junctions), each as /locate reports it: `edge.access`,
        `edge.classification`, `edge.speeds`, `edge.forward`, `edge_info.names`...
        """
        refs, _ = self.ways[way_id]
        found = {}
        for a, b in zip(refs, refs[1:], strict=False):
            (lon_a, lat_a), (lon_b, lat_b) = self.node(a), self.node(b)
            located = self.actor.locate({
                "locations": [{"lon": (lon_a + lon_b) / 2, "lat": (lat_a + lat_b) / 2,
                               "radius": 1}],
                "verbose": True,
                "costing": "none",
            })
            for edge in located[0].get("edges") or []:
                if edge["edge_info"]["way_id"] == way_id:
                    found[edge["edge_id"]["value"]] = edge
        return list(found.values())

    def node(self, node_id: int) -> LonLat:
        return self._nodes_by_id()[node_id]

    def _nodes_by_id(self) -> dict[int, LonLat]:
        if self._nodes is None:
            self._nodes = {
                node.id: (node.location.lon, node.location.lat)
                for node in osmium.FileProcessor(str(self.pbf), osmium.osm.NODE)
            }
        return self._nodes


def _location(point: LonLat) -> dict:
    # radius 0 / reachability 0: use the nearest edge, even on a tiny
    # network where nothing is reachable from many nodes.
    return {"lon": point[0], "lat": point[1], "minimum_reachability": 0, "radius": 0}


def _is_no_route(exc: RuntimeError) -> bool:
    """Valhalla's "no path" / "no suitable edges" errors (codes 442, 171 ...)."""
    text = str(exc).lower()
    return "no path could be found" in text or "no suitable edges" in text
