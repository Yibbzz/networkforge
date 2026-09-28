"""
Check the NetworkForge GeoPackage in real QGIS, via `qgis_process`.

Run inside QGIS's environment (e.g. the qgis/qgis Docker image):

    QT_QPA_PLATFORM=offscreen python3 tests/qgis/check_with_qgis.py FIXTURE_DIR

FIXTURE_DIR comes from `uv run python -m tests.qgis.make_fixture FIXTURE_DIR`.
Standard library only (it runs in QGIS's Python, not the project's).

For each case in expected.json, runs QGIS's "Shortest path (point to
point)" on the edges layer, filtered to the mode's column and with the
mode's direction field - exactly as the README tells QGIS users to - and
compares QGIS's travel cost with the expected one. Then runs "Service
area (from point)" once to check the speed and direction fields work.
Exits non-zero if anything fails.
"""

import json
import subprocess
import sys
from pathlib import Path

DIRECTION = {"car": "car_direction", "bike": "bike_direction", "walk": None}
RELATIVE_TOLERANCE = 1e-6

# QGIS's network analysis algorithms refuse to run without a project;
# an empty one is enough.
EMPTY_PROJECT = (
    "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
    '<qgis version="3.34"/>\n'
)
PROJECT: Path | None = None


def run(algorithm: str, params: dict) -> dict:
    """Run a Processing algorithm; return its results (raises on failure)."""
    args = ["qgis_process", "--json", "run", algorithm, f"--PROJECT_PATH={PROJECT}",
            "--distance_units=meters", "--area_units=m2", "--ellipsoid=NONE", "--"]
    args += [f"{key}={value}" for key, value in params.items()]
    process = subprocess.run(args, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"{algorithm} failed:\n{process.stderr or process.stdout}")
    return json.loads(process.stdout)["results"]


def network_params(gpkg: Path, mode: str, strategy: str) -> dict:
    params = {
        # The same filter the README tells users to set on the layer.
        "INPUT": f'{gpkg}|layername=edges|subset="{mode}" = 1',
        "STRATEGY": 1 if strategy == "fastest" else 0,
        "DEFAULT_DIRECTION": 2,  # both
    }
    if DIRECTION[mode]:
        params |= {"DIRECTION_FIELD": DIRECTION[mode], "VALUE_FORWARD": "forward",
                   "VALUE_BACKWARD": "backward", "VALUE_BOTH": "both"}
    if strategy == "fastest":
        params |= {"SPEED_FIELD": "speed_kph", "DEFAULT_SPEED": 30}
    return params


def main(fixture: Path) -> int:
    global PROJECT
    PROJECT = fixture / "empty.qgs"
    PROJECT.write_text(EMPTY_PROJECT)
    gpkg = fixture / "network.gpkg"
    expected = json.loads((fixture / "expected.json").read_text())
    crs = expected["crs"]
    failures = []

    for case in expected["cases"]:
        params = network_params(gpkg, case["mode"], case["strategy"]) | {
            "START_POINT": f"{case['start'][0]},{case['start'][1]} [{crs}]",
            "END_POINT": f"{case['end'][0]},{case['end'][1]} [{crs}]",
            "OUTPUT": "TEMPORARY_OUTPUT",
        }
        try:
            cost = float(run("native:shortestpathpointtopoint", params)["TRAVEL_COST"])
        except (RuntimeError, KeyError, ValueError) as exc:
            failures.append(f"{case['name']}: {exc}")
            continue
        ok = abs(cost - case["expected"]) <= RELATIVE_TOLERANCE * max(1.0, abs(case["expected"]))
        print(f"{'ok  ' if ok else 'FAIL'} {case['name']}: QGIS {cost:.6f}, "
              f"expected {case['expected']:.6f}")
        if not ok:
            failures.append(case["name"])

    # Service area: the speed and direction fields are accepted and give lines.
    first = expected["cases"][0]
    try:
        results = run("native:serviceareafrompoint", network_params(gpkg, "car", "fastest") | {
            "START_POINT": f"{first['start'][0]},{first['start'][1]} [{crs}]",
            "TRAVEL_COST2": 60 / 3600,  # one minute, in hours
            "OUTPUT_LINES": "TEMPORARY_OUTPUT",
        })
        print(f"ok   service area ran: {sorted(results)}")
    except RuntimeError as exc:
        failures.append(f"service area: {exc}")

    if failures:
        print(f"\n{len(failures)} failure(s):\n  " + "\n  ".join(failures))
        return 1
    print(f"\nAll {len(expected['cases'])} QGIS routes match.")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "qgis-fixture")))
