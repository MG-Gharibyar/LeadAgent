import json
import math

from leadagent.geo import GeoIndex, apply_area_match, haversine_km, parse_area, resolve_areas
from leadagent.models import Lead


def make_index(tmp_path):
    path = tmp_path / "geo.json"
    path.write_text(
        json.dumps(
            {
                "places": {
                    "karlsruhe": {
                        "name": "Karlsruhe",
                        "latitude": 49.0069,
                        "longitude": 8.4037,
                        "population": 300000,
                    },
                    "ettlingen": {
                        "name": "Ettlingen",
                        "latitude": 48.94,
                        "longitude": 8.407,
                        "population": 39000,
                    },
                    "stuttgart": {
                        "name": "Stuttgart",
                        "latitude": 48.7758,
                        "longitude": 9.1829,
                        "population": 630000,
                    },
                }
            }
        )
    )
    return GeoIndex(str(path))


def test_area_parse_and_match(tmp_path):
    index = make_index(tmp_path)
    assert parse_area("Karlsruhe:50") == ("Karlsruhe", 50)
    lead = Lead("Synthetic", "https://synthetic.example", city="Ettlingen")
    assert (
        apply_area_match(
            lead,
            resolve_areas(["Karlsruhe:50", "Stuttgart:100"], index),
            index,
        )
        == "MATCH"
    )
    assert lead.campaign_region == "Karlsruhe"
    assert "Karlsruhe:50" in lead.search_areas


def test_outside_and_review(tmp_path):
    index = make_index(tmp_path)
    area = resolve_areas(["Karlsruhe:5"], index)
    assert (
        apply_area_match(Lead("X", "https://x.example", city="Stuttgart"), area, index) == "OUTSIDE"
    )
    unknown = Lead("Y", "https://y.example", city="Nowhere")
    assert apply_area_match(unknown, area, index) == "REVIEW"
    assert unknown.location_review_required


def test_haversine():
    assert math.isclose(haversine_km(49.0, 8.4, 50.0, 8.4), 111.2, rel_tol=0.02)


def test_load_areas_refreshes_stale_index_once(tmp_path, monkeypatch):
    from leadagent import geo

    path = tmp_path / "geo.json"
    path.write_text(json.dumps({"places": {}}))

    def fake_update(target):
        assert str(path) == target
        path.write_text(
            json.dumps(
                {
                    "places": {
                        "stuttgart": {
                            "name": "Stuttgart",
                            "latitude": 48.7758,
                            "longitude": 9.1829,
                            "population": 630000,
                        }
                    }
                }
            )
        )
        return 1

    monkeypatch.setattr(geo, "update_geo_database", fake_update)
    index, areas = geo.load_areas(["Stuttgart:35"], str(path))
    assert index.resolve("Stuttgart") is not None
    assert areas[0].city == "Stuttgart"
    assert areas[0].radius_km == 35


def test_major_city_fallback_when_local_index_is_incomplete(tmp_path):
    path = tmp_path / "geo.json"
    path.write_text(json.dumps({"places": {}}))
    index = GeoIndex(str(path))
    stuttgart = index.resolve("Stuttgart")
    assert stuttgart is not None
    assert stuttgart.name == "Stuttgart"
    areas = resolve_areas(["Stuttgart:35"], index)
    assert areas[0].radius_km == 35


def test_area_match_prefers_discovery_coordinates(tmp_path):
    index = make_index(tmp_path)
    lead = Lead(
        "Synthetic",
        "https://synthetic.example",
        city="Unresolvable Suburb",
        latitude=48.80,
        longitude=9.20,
        location_resolution_source="OpenStreetMap discovery geometry",
    )
    area = resolve_areas(["Stuttgart:35"], index)
    assert apply_area_match(lead, area, index) == "MATCH"
    assert lead.campaign_region == "Stuttgart"
