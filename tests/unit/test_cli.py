"""The command-line interface: arguments, JSON output, exit codes (no download)."""

import json
import subprocess
import sys

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from networkforge.cli import (
    EXIT_INPUT,
    EXIT_OK,
    EXIT_USAGE,
    _join_negative_bbox,
    _tags,
    main,
)
from networkforge.errors import InputError
from networkforge.presets import PRESETS


@pytest.fixture
def custom_file(tmp_path):
    path = tmp_path / "custom.geojson"
    gpd.GeoDataFrame(
        {"highway": ["primary", None]},
        geometry=[LineString([(-3.19, 55.95), (-3.18, 55.96)]),
                  LineString([(-3.18, 55.95), (-3.17, 55.96)])],
        crs="EPSG:4326",
    ).to_file(path)
    return path


def json_lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.startswith("networkforge ")


def test_runs_as_a_module():
    result = subprocess.run([sys.executable, "-m", "networkforge", "presets"],
                            capture_output=True, text=True, check=True)
    assert "primary_road" in result.stdout


def test_presets_json_lists_every_preset(capsys):
    assert main(["presets", "--json"]) == EXIT_OK
    events = json_lines(capsys)
    assert [e["name"] for e in events] == list(PRESETS)
    assert events[0]["modes"] == ["drive", "drive_service"]


def test_check_fills_gaps_with_the_preset(custom_file, capsys):
    assert main(["check", "--custom", str(custom_file), "--preset", "cycleway", "--json"]) == 0
    (done,) = json_lines(capsys)
    assert done["features"] == 2
    assert done["modes"] == {"drive, drive_service, walk, bike": 1, "bike": 1}


def test_check_reports_tag_problems_as_json(custom_file, capsys):
    code = main(["check", "--custom", str(custom_file), "--tag", "maxspeed=fast", "--json"])

    assert code == EXIT_INPUT
    (error,) = json_lines(capsys)
    assert error["type"] == "InvalidTagsError"
    assert error["guide"] == "fixing-tag-errors"
    assert len(error["problems"]) == 3  # 2x bad maxspeed, 1x missing highway


def test_check_with_extent_checks_geometry(custom_file, tmp_path, capsys):
    code = main(["check", "--custom", str(custom_file), "--preset", "cycleway",
                 "--bbox", "10,10,11,11", "--json"])
    assert code == EXIT_INPUT
    assert "outside the bounding box" in json_lines(capsys)[0]["message"]


def test_missing_file_is_an_input_error(tmp_path, capsys):
    code = main(["check", "--custom", str(tmp_path / "nope.gpkg"), "--json"])
    assert code == EXIT_INPUT
    assert "doesn't exist" in json_lines(capsys)[0]["message"]


def test_build_needs_an_output(custom_file, capsys):
    code = main(["build", "--bbox=-3.2,55.9,-3.1,56.0", "--custom", str(custom_file), "--json"])
    assert code == EXIT_INPUT
    assert "Nothing to write" in json_lines(capsys)[0]["message"]


@pytest.mark.parametrize("bbox, message", [
    ("1,2,3", "W,S,E,N numbers"),
    ("a,b,c,d", "W,S,E,N numbers"),
    ("3,2,1,4", "west < east"),
])
def test_bad_bbox_is_an_input_error(custom_file, capsys, bbox, message):
    code = main(["build", f"--bbox={bbox}", "--custom", str(custom_file),
                 "--out", "x.osm", "--json"])
    assert code == EXIT_INPUT
    assert message in json_lines(capsys)[0]["message"]


def test_unknown_option_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as info:
        main(["build", "--nope"])
    assert info.value.code == EXIT_USAGE


def test_unknown_preset_is_a_usage_error(custom_file):
    with pytest.raises(SystemExit) as info:
        main(["check", "--custom", str(custom_file), "--preset", "primary road"])
    assert info.value.code == EXIT_USAGE


def test_negative_bbox_needs_no_equals_sign():
    assert _join_negative_bbox(["build", "--bbox", "-4.6,54.1,-4.4,54.2", "--json"]) == [
        "build", "--bbox=-4.6,54.1,-4.4,54.2", "--json"]
    assert _join_negative_bbox(["--bbox", "4.6,54.1,4.8,54.2"]) == ["--bbox", "4.6,54.1,4.8,54.2"]


def test_tag_arguments():
    assert _tags(["maxspeed=40 mph", " lanes = 2 "]) == {"maxspeed": "40 mph", "lanes": "2"}
    with pytest.raises(InputError, match="KEY=VALUE"):
        _tags(["maxspeed"])
