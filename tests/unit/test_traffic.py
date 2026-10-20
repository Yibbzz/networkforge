"""Traffic settings and the week of speed factors (no Valhalla needed)."""

import json

import numpy as np
import pytest

from networkforge.errors import InputError
from networkforge.traffic import (
    BUCKETS_PER_DAY,
    BUCKETS_PER_WEEK,
    parse_bands,
    read_traffic_settings,
    street_bands,
    week_factors,
)

PEAK = {"name": "Morning peak", "days": "Mo-Fr", "time": "07:00-09:30",
        "roads": ["primary", "secondary"], "factor": 0.6}


def bucket(day: int, hh: int, mm: int = 0) -> int:
    """Valhalla's bucket: 5 minutes each, Sunday (day 0) 00:00 first."""
    return day * BUCKETS_PER_DAY + (hh * 60 + mm) // 5


def test_bucket_zero_is_sunday_midnight():
    assert bucket(0, 0) == 0
    assert bucket(1, 8) == 384               # Monday 08:00
    assert bucket(6, 23, 55) == BUCKETS_PER_WEEK - 1


def test_a_band_covers_its_days_and_hours_only():
    factors = week_factors(parse_bands([PEAK]), "primary")
    assert factors[bucket(1, 7)] == pytest.approx(0.6)          # Monday 07:00, first bucket
    assert factors[bucket(5, 9, 25)] == pytest.approx(0.6)      # Friday 09:25, last bucket
    assert factors[bucket(1, 9, 30)] == 1                       # ends at 09:30
    assert factors[bucket(1, 6, 55)] == 1
    assert factors[bucket(0, 8)] == 1                           # Sunday
    assert factors[bucket(6, 8)] == 1                           # Saturday
    assert np.count_nonzero(factors < 1) == 5 * 30              # 5 days x 2.5 h


def test_links_count_as_their_road_and_other_roads_are_untouched():
    bands = parse_bands([PEAK])
    assert week_factors(bands, "primary_link") is not None
    assert week_factors(bands, "residential") is None


def test_all_roads():
    bands = parse_bands([{**PEAK, "roads": "all"}])
    assert week_factors(bands, "residential") is not None
    assert week_factors(bands, "footway") is not None


def test_a_band_can_run_past_midnight_and_wrap_the_week():
    night = parse_bands([{**PEAK, "days": "Sa", "time": "22:00-02:00", "factor": 1.2}])
    factors = week_factors(night, "primary")
    assert factors[bucket(6, 23)] == pytest.approx(1.2)        # Saturday 23:00
    assert factors[bucket(0, 1, 55)] == pytest.approx(1.2)     # Sunday 01:55: wraps the week
    assert factors[bucket(0, 2)] == 1


def test_overlapping_bands_the_slowest_wins():
    bands = parse_bands([{**PEAK, "roads": "all", "time": "07:00-19:00", "factor": 0.9}, PEAK])
    factors = week_factors(bands, "primary")
    assert factors[bucket(1, 8)] == pytest.approx(0.6)
    assert factors[bucket(1, 12)] == pytest.approx(0.9)


@pytest.mark.parametrize(("days", "expected"), [
    ("Mo-Fr", (1, 2, 3, 4, 5)),
    ("Sa,Su", (0, 6)),
    ("Mo-Su", (0, 1, 2, 3, 4, 5, 6)),
    ("Fr-Mo", (0, 1, 5, 6)),
    ("we", (3,)),
])
def test_days(days, expected):
    assert parse_bands([{**PEAK, "days": days}])[0].days == expected


def test_days_default_to_every_day():
    band = {key: value for key, value in PEAK.items() if key != "days"}
    assert parse_bands([band])[0].days == (0, 1, 2, 3, 4, 5, 6)


def test_whole_day():
    band = parse_bands([{**PEAK, "time": "00:00-24:00"}])[0]
    assert len(band.buckets()) == 5 * BUCKETS_PER_DAY


def test_every_problem_is_listed_at_once():
    with pytest.raises(InputError) as raised:
        parse_bands([{"days": "Mo-Fx", "time": "7-9", "roads": ["primry"], "factor": 6,
                      "colour": "red"}])
    message = str(raised.value)
    for words in ("unknown setting(s) colour", "days 'Mo-Fx'", "time must be like",
                  "unknown road class(es) primry", "factor 6"):
        assert words in message
    assert raised.value.guide == "traffic.md#settings"
    assert len(raised.value.issues) == 5


@pytest.mark.parametrize("time", ["07:02-09:30", "09:00-09:00", "25:00-26:00"])
def test_bad_times(time):
    with pytest.raises(InputError):
        parse_bands([{**PEAK, "time": time}])


def test_no_bands():
    with pytest.raises(InputError, match="at least one"):
        parse_bands([])


def test_settings_from_toml_and_json(tmp_path):
    (tmp_path / "t.toml").write_text(
        '[[band]]\nname = "Morning peak"\ndays = "Mo-Fr"\ntime = "07:00-09:30"\n'
        'roads = ["primary", "secondary"]\nfactor = 0.6\n')
    (tmp_path / "t.json").write_text(json.dumps({"band": [PEAK]}))
    assert read_traffic_settings(tmp_path / "t.toml") == read_traffic_settings(tmp_path / "t.json")


def test_unreadable_settings(tmp_path):
    (tmp_path / "t.toml").write_text("[[band]\n")
    with pytest.raises(InputError, match="Can't read"):
        read_traffic_settings(tmp_path / "t.toml")
    with pytest.raises(InputError, match="doesn't exist"):
        read_traffic_settings(tmp_path / "missing.toml")


# ---------------------------------------------------------------- single streets

def streets_layer(rows, crs="EPSG:4326"):
    import geopandas as gpd
    from shapely.geometry import LineString, MultiLineString, Point

    def geometry(coords):
        if coords is None:
            return None
        if isinstance(coords, str):
            return Point(0, 0)
        if isinstance(coords[0][0], tuple):
            return MultiLineString(coords)
        return LineString(coords)
    return gpd.GeoDataFrame([fields for _, fields in rows],
                            geometry=[geometry(c) for c, _ in rows], crs=crs,
                            index=range(10, 10 + len(rows)))


LINE = [(0.0, 51.0), (0.001, 51.0)]


def test_street_rows_by_line_and_by_way_id():
    streets = street_bands(streets_layer([
        (LINE, {"days": "Mo-Fr", "time": "07:00-09:30", "factor": 0.5, "osm_id": None}),
        (None, {"days": None, "time": "16:00-18:00", "factor": "0.7", "osm_id": 123.0,
                "direction": "Forward"}),
    ]))
    line, way = streets
    assert (line.feature, line.way, line.direction) == (10, None, "both")
    assert line.line == tuple(LINE)
    assert (way.feature, way.way, way.line, way.direction) == (11, 123, None, "forward")
    assert way.band.days == (0, 1, 2, 3, 4, 5, 6)       # no days: every day
    assert way.band.factor == pytest.approx(0.7)


def test_a_multi_line_is_one_row_per_part():
    streets = street_bands(streets_layer([
        ([tuple(LINE), ((0.0, 51.001), (0.001, 51.001))], {"time": "07:00-09:00",
                                                          "factor": 0.5}),
    ]))
    assert [s.feature for s in streets] == [10, 10]


def test_street_lines_are_read_in_wgs84():
    streets = street_bands(streets_layer([([(500000, 6200000), (500100, 6200000)],
                                           {"time": "07:00-09:00", "factor": 0.5})],
                                         crs="EPSG:32630"))
    lon, lat = streets[0].line[0]
    assert -4 < lon < -2 and 55 < lat < 57


def test_every_street_problem_names_its_feature():
    with pytest.raises(InputError) as raised:
        street_bands(streets_layer([
            (LINE, {"time": "7-9", "factor": 0.5}),
            (LINE, {"time": "07:00-09:00", "factor": None, "direction": "up"}),
            (None, {"time": "07:00-09:00", "factor": 0.5}),
            ("point", {"time": "07:00-09:00", "factor": 0.5}),
        ]))
    issues = {(i["feature"], i["message"].split(" ")[0]) for i in raised.value.issues}
    assert issues == {(10, "time"), (11, "factor"), (11, "direction"), (12, "has"),
                      (13, "is")}
    assert raised.value.guide == "traffic.md#single-streets"


def test_a_layer_without_a_crs():
    layer = streets_layer([(LINE, {"time": "07:00-09:00", "factor": 0.5})]).set_crs(
        None, allow_override=True)
    with pytest.raises(InputError, match="coordinate reference system"):
        street_bands(layer)
