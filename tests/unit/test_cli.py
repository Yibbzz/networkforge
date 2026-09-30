"""The command-line interface: arguments, JSON output, exit codes (no download)."""

import json
import subprocess
import sys
from importlib.metadata import version

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

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


# ---------------------------------------------------------------- v0.4 plugin support

@pytest.fixture
def id_file(tmp_path):
    """GeoPackage like a QGIS export: fid 101-103, a 'length' field, one bad tag."""
    path = tmp_path / "custom.gpkg"
    gdf = gpd.GeoDataFrame(
        {"highway": ["primary", "cycleway", "primary"],
         "maxspeed": ["40 mph", None, "fast"],
         "ref_id": ["a", "b", "c"],
         "length": [1.0, 2.0, 3.0]},
        geometry=[LineString([(-3.19, 55.95), (-3.18, 55.96)]),
                  LineString([(-3.18, 55.95), (-3.17, 55.96)]),
                  LineString([(-3.17, 55.95), (-3.00, 55.96)])],
        crs="EPSG:4326", index=pd.Index([101, 102, 103], name="fid"),
    )
    gdf.to_file(path, driver="GPKG", index=True)
    return path


def run_json(capsys, *args):
    code = main([*args, "--json"])
    return code, json_lines(capsys)


def test_issues_name_features_by_gpkg_fid(id_file, capsys):
    code, events = run_json(capsys, "check", "--custom", str(id_file), "--id-field", "fid")
    assert code == EXIT_INPUT
    error = events[-1]
    assert error["issues"] == [
        {"feature": 103, "message": "maxspeed='fast' is not a valid OSM speed"}]


def test_issues_name_features_by_an_attribute(id_file, capsys):
    code, events = run_json(capsys, "check", "--custom", str(id_file), "--id-field", "ref_id")
    assert [i["feature"] for i in events[-1]["issues"]] == ["c"]


def test_without_id_field_features_are_row_numbers(id_file, capsys):
    _, events = run_json(capsys, "check", "--custom", str(id_file))
    assert [i["feature"] for i in events[-1]["issues"]] == [2]


def test_unknown_id_field_lists_attributes(id_file, capsys):
    code, events = run_json(capsys, "check", "--custom", str(id_file), "--id-field", "nope")
    assert code == EXIT_INPUT
    assert "Attributes: highway, maxspeed, ref_id, length" in events[-1]["message"]


def test_repeated_ids_are_rejected(id_file, capsys):
    code, events = run_json(capsys, "check", "--custom", str(id_file), "--id-field", "highway")
    assert code == EXIT_INPUT
    assert "must be unique; repeated: primary" in events[-1]["message"]


def test_warnings_are_json_events_with_features_and_fields(id_file, capsys):
    _, events = run_json(capsys, "check", "--custom", str(id_file), "--id-field", "fid",
                         "--tag", "maxspeed=30 mph", "--overwrite-tags",
                         "--bbox=-3.2,55.9,-3.1,56.0")
    warnings = [e for e in events if e["event"] == "warning"]
    assert {"event": "warning", "fields": ["length"],
            "message": "Ignoring custom attribute(s) length: the names are used internally "
                       "and aren't OSM tags."} in warnings
    assert any(w.get("features") == [103] and "partly outside" in w["message"] for w in warnings)
    assert events[-1]["event"] == "done"


def test_geometry_issues_name_features(tmp_path, capsys):
    path = tmp_path / "mixed.geojson"
    gpd.GeoDataFrame({"highway": ["primary", "primary"]},
                     geometry=[LineString([(-3.19, 55.95), (-3.18, 55.96)]), Point(-3.18, 55.95)],
                     crs="EPSG:4326").to_file(path)
    code, events = run_json(capsys, "check", "--custom", str(path), "--bbox=-3.2,55.9,-3.1,56.0")
    assert code == EXIT_INPUT
    assert events[-1]["issues"] == [{"feature": 1, "message": "Point, not a line"}]


def test_text_mode_keeps_stdout_free_of_json(id_file, capsys):
    main(["check", "--custom", str(id_file)])
    captured = capsys.readouterr()
    assert not captured.out.lstrip().startswith("{")
    assert "maxspeed='fast'" in captured.err


def test_info_describes_the_engine(capsys):
    from networkforge.export import GPKG_ANALYSIS_COLUMNS
    from networkforge.inputs import MAX_OVERPASS_AREA_KM2
    from networkforge.validation import KNOWN_HIGHWAYS

    code, (info,) = run_json(capsys, "info")
    assert code == EXIT_OK
    assert info["version"] == version("networkforge")
    assert list(info["presets"]) == list(PRESETS)
    assert info["presets"]["cycleway"] == {"tags": PRESETS["cycleway"], "modes": ["bike"]}
    assert info["tag_values"]["highway"] == sorted(KNOWN_HIGHWAYS)
    assert "motor_vehicle" in info["tag_values"] and "bicycle" in info["tag_values"]
    assert info["max_overpass_area_km2"] == MAX_OVERPASS_AREA_KM2
    assert info["gpkg_edge_columns"] == list(GPKG_ANALYSIS_COLUMNS)
