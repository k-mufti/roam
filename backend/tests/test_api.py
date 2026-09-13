"""API smoke tests.

These hit a real database, so they skip cleanly when one is not reachable —
the rest of the suite is pure-unit and must stay runnable without Postgres.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine


def _database_available() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(), reason="requires the Postgres+PostGIS container"
)


@pytest.fixture(scope="module")
def client():
    from app.api.main import app

    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_facets_shape(client):
    data = client.get("/api/facets").json()
    assert data["city"]
    assert {"lat", "lng"} <= data["center"].keys()
    for key in ("categories", "tags", "price_tiers", "sources"):
        assert isinstance(data[key], list)


def test_places_listing(client):
    places = client.get("/api/places?limit=5").json()
    assert isinstance(places, list)
    if places:
        place = places[0]
        assert {"id", "name", "lat", "lng", "category", "source_signals"} <= place.keys()
        # The spec describes source_signals as a list on the Place; it is a
        # child table relationally, and the API presents it as described.
        assert isinstance(place["source_signals"], list)


def test_places_are_score_ordered(client):
    places = client.get("/api/places?limit=20").json()
    scores = [p["composite_score"] for p in places if p["composite_score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_radius_filter_narrows_results(client):
    wide = client.get("/api/places?lat=40.4168&lng=-3.7038&radius_m=20000&limit=500").json()
    tight = client.get("/api/places?lat=40.4168&lng=-3.7038&radius_m=800&limit=500").json()
    assert len(tight) <= len(wide)


def test_corroboration_filter(client):
    multi = client.get("/api/places?min_sources=2&limit=200").json()
    assert all(p["source_count"] >= 2 for p in multi)


def test_category_filter(client):
    museums = client.get("/api/places?category=museum&limit=50").json()
    assert all(p["category"] == "museum" for p in museums)


def test_score_breakdown_endpoint(client):
    places = client.get("/api/places?limit=1").json()
    if not places:
        pytest.skip("no places ingested")
    data = client.get(f"/api/places/{places[0]['id']}/score").json()
    assert data["place_id"] == places[0]["id"]
    if data["breakdown"]:
        assert "signals" in data["breakdown"]
        assert "model_version" in data["breakdown"]


def test_unknown_place_score_is_404(client):
    response = client.get("/api/places/00000000-0000-0000-0000-000000000000/score")
    assert response.status_code == 404


def test_itinerary_generation(client):
    response = client.post(
        "/api/itinerary",
        json={"start_date": "2026-09-18", "days": 3, "pace": "moderate", "max_price_tier": 4},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["days"]) == 3
    for day in data["days"]:
        # Times must be ordered and non-overlapping within a day.
        for earlier, later in zip(day["stops"], day["stops"][1:], strict=False):
            assert earlier["end_time"] <= later["start_time"] or later["crosses_midnight"]
        # Every stop carries what the map needs to draw it.
        for stop in day["stops"]:
            assert stop["lat"] and stop["lng"] and stop["position"] >= 1


def test_itinerary_rejects_bad_input(client):
    assert client.post("/api/itinerary", json={"start_date": "2026-09-18", "days": 0}).status_code == 422
    assert client.post("/api/itinerary", json={"days": 3}).status_code == 422


def test_provenance_reports_live_vs_fixture(client):
    data = client.get("/api/meta/provenance").json()
    assert data["city"]
    for source in data["sources"]:
        assert source["mode"] in {"live", "fixture", "failed", "pending"}
        assert isinstance(source["is_live"], bool)
