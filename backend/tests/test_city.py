"""The healthcare team's districts, facilities and environment: served as copied, joined by district_key, and the feature is off without the data."""

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import schemas as S
from app.city import CityData, CityError, DEFAULT_DIR
from app.main import app
from app.schemas.city import CityDistricts, CityFacility


@pytest.fixture(scope="module")
def zones():
    from app.sim.topology import build

    return {z.id: z.center for z in build().topology.zones}


@pytest.fixture(scope="module")
def city(zones):
    return CityData(zone_centers=zones)


def test_the_data_is_complete_and_every_key_joins(city):
    assert city.available
    d = city.districts()
    assert [x.district_key for x in d.districts] == [str(k) for k in range(1001, 1016)]
    assert d.month == d.months[-1] == "2026-09" and d.months[0] == "2025-01" and len(d.months) == 21
    keys = {x.district_key for x in d.districts}
    facilities = city.facilities()
    assert len(facilities) == 81 and {f.district_key for f in facilities} <= keys
    assert sum(1 for f in facilities if f.kind == "hospital") == 30
    assert sum(x.facilities.hospitals + x.facilities.clinics + x.facilities.doctors for x in d.districts) == 81
    assert next(x for x in d.districts if x.district_key == "1013").facilities.hospitals == 8


def test_the_values_are_the_ones_in_the_files_not_computed_here(city):
    """Re-read the extract by hand for one district and month and compare every column the API shows."""
    d = city.districts("2026-09")
    mine = next(x for x in d.districts if x.district_key == "1009")
    with (DEFAULT_DIR / "environment_monthly.csv").open(encoding="utf-8") as f:
        row = next(r for r in csv.DictReader(f) if r["district_key"] == "1009" and r["month"] == "2026-09-01")
    assert mine.metrics.aqi_mean == float(row["p3_avg_aqi"]) and mine.metrics.bin_fill_pct == float(row["p2_avg_bin_fill_pct"])
    assert mine.metrics.water_pollution_index == float(row["pw_water_pollution_index"]) and mine.metrics.stress.overall == float(row["environmental_stress_index"])
    assert mine.metrics.residents == int(float(row["residents_registered"]))
    assert mine.match_kind == "direct" and mine.our_zone_ids == ["zone-cbd"]


def test_every_district_has_a_position_inside_the_capital_and_values_in_range(city):
    for x in city.districts().districts:
        assert 29.9 < x.position.lat < 30.1 and 31.6 < x.position.lng < 31.9
        s = x.metrics.stress
        assert all(0 <= v <= 100 for v in (s.air, s.waste, s.water, s.heat, s.overall))
        assert x.metrics.aqi_mean > 0 and 0 <= x.metrics.bin_fill_pct <= 100 and x.metrics.ecoli_positive_pct >= 0


def test_months_are_validated(city):
    assert city.districts("2025-01").month == "2025-01" and city.districts("2025-01-01").month == "2025-01"
    for bad, status in (("2026-13", 422), ("abc", 422), ("2026-9", 422), ("2030-01", 404), ("2024-12", 404)):
        with pytest.raises(CityError) as e:
            city.districts(bad)
        assert e.value.status == status, bad


def test_the_feature_is_off_not_broken_when_the_data_is_missing(tmp_path):
    off = CityData(tmp_path)
    assert not off.available
    for call in (off.districts, off.facilities):
        with pytest.raises(CityError) as e:
            call()
        assert e.value.status == 404 and e.value.code == "no_city_data"
    (tmp_path / "districts.csv").write_text("district_key\n")  # a half-installed folder is also off
    assert not CityData(tmp_path).available


def test_endpoints_validate_and_report_errors_in_the_contract_shape():
    with TestClient(app) as c:
        d = CityDistricts.model_validate(c.get("/api/v1/city/districts").json())
        assert len(d.districts) == 15 and d.source.sha256 and d.source.simulated
        older = CityDistricts.model_validate(c.get("/api/v1/city/districts?month=2025-06").json())
        assert older.month == "2025-06" and older.districts[0].metrics.month == "2025-06"
        assert older.districts[0].metrics != d.districts[0].metrics
        facilities = [CityFacility.model_validate(f) for f in c.get("/api/v1/city/facilities").json()]
        assert len(facilities) == 81
        for q, status, code in (("month=nope", 422, "invalid_month"), ("month=2031-01", 404, "no_month")):
            r = c.get(f"/api/v1/city/districts?{q}")
            assert r.status_code == status and r.json()["code"] == code
            S.ErrorResponse.model_validate(r.json())
        raw = c.get("/api/v1/city/districts").json()
        assert "aqiMean" in raw["districts"][0]["metrics"] and "districtKey" in raw["districts"][0] and raw["districts"][0]["position"].keys() == {"lat", "lng"}


def test_districts_are_drawn_on_the_zone_they_match_not_at_their_own_coordinates(city, zones):
    import math

    def km(a, b):
        return math.hypot((a.lat - b.lat) * 111, (a.lng - b.lng) * 96)

    d = {x.code: x for x in city.districts().districts}
    assert d["NAC-PARK"].placed_at is None and d["NAC-PARK"].match_kind == "none"  # no counterpart, so it is not placed
    for x in city.districts().districts:
        if x.match_kind == "none":
            continue
        assert x.placed_at is not None
        assert min(km(x.placed_at, zones[z]) for z in x.our_zone_ids) < 1.0  # within a ring of the centre of one of its zones
    # the only district matched to a zone of its own with nobody else on it sits exactly on the centre
    assert d["NAC-GOV"].placed_at is not None and km(d["NAC-GOV"].placed_at, zones["zone-gov"]) < 1.0
    assert len({(x.placed_at.lat, x.placed_at.lng) for x in d.values() if x.placed_at}) == 14  # no two symbols on the same spot
    assert d["NAC-GOV"].position.lng == 31.7  # their own coordinates are still reported unchanged


def test_facilities_keep_their_spread_around_their_district(city):
    districts = {x.district_key: x for x in city.districts().districts}
    for f in city.facilities():
        x = districts[f.district_key]
        if x.placed_at is None:
            assert f.placed_at is None
            continue
        assert abs((f.placed_at.lat - x.placed_at.lat) - (f.position.lat - x.position.lat)) < 1e-9
        assert abs((f.placed_at.lng - x.placed_at.lng) - (f.position.lng - x.position.lng)) < 1e-9


def test_without_the_zones_nothing_is_placed():
    c = CityData()
    assert all(x.placed_at is None for x in c.districts().districts) and all(f.placed_at is None for f in c.facilities())
