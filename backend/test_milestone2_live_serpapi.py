"""
Project Musafir — Component 21: Live SerpApi Validation
Performs live API verification of anchored restaurant and café discovery
and verifies normalized model compatibility with real SerpApi Google Maps responses.
"""

import os
import sys
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

from app.services.restaurant_service import (
    build_restaurant_search_params,
    fetch_restaurants_from_serpapi,
    normalize_restaurants_response,
)
from app.services.place_service import (
    build_place_search_params,
    fetch_places_from_serpapi,
    normalize_places_response,
)
from app.services.route_service import optimize_route
from app.schemas.route import LocationPoint, RouteStop, RouteRequest


def test_live_serpapi():
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        print("SERPAPI_API_KEY is not set. Skipping live test.")
        return

    print("=" * 65)
    print("MILESTONE 2: LIVE SERPAPI RESTAURANT & ROUTE VALIDATION")
    print("=" * 65)

    # 1. Live Restaurant Search Anchored Near Hotel Coordinates
    # Coordinates of Bolgatty Island / Grand Hyatt Kochi: (9.9880, 76.2625)
    print("\n[1/3] Testing Live Anchored Restaurant Search near Grand Hyatt Kochi...")
    params = build_restaurant_search_params(
        destination="Kochi, Kerala",
        query="lunch",
        location_anchor="Grand Hyatt Kochi Bolgatty",
        latitude=9.9880,
        longitude=76.2625,
        meal_type="lunch",
        api_key=api_key,
    )
    print(f"  * Query: '{params['q']}'")
    print(f"  * Center Coords (ll): {params.get('ll')}")

    raw_data = fetch_restaurants_from_serpapi(params)
    restaurants = normalize_restaurants_response(raw_data, limit=5)
    print(f"  * Retrieved {len(restaurants)} live normalized restaurants:")
    for r in restaurants:
        print(f"    - {r.name} | Rating: {r.rating} ({r.review_count} revs) | Coords: ({r.latitude}, {r.longitude}) | Cuisine: {r.cuisine} | ID: {r.data_id}")
        assert r.name, "Restaurant name cannot be empty"
        assert r.data_id, "data_id cannot be empty"
        if r.latitude and r.longitude:
            assert isinstance(r.latitude, float)
            assert isinstance(r.longitude, float)

    assert len(restaurants) > 0, "Expected at least 1 restaurant from live search"

    # 2. Live Cafe Search Anchored Near Fort Kochi
    # Fort Kochi coords: (9.9658, 76.2421)
    print("\n[2/3] Testing Live Cafe Search near Fort Kochi...")
    cafe_params = build_restaurant_search_params(
        destination="Kochi, Kerala",
        category="cafe",
        location_anchor="Fort Kochi",
        latitude=9.9658,
        longitude=76.2421,
        api_key=api_key,
    )
    print(f"  * Query: '{cafe_params['q']}'")
    print(f"  * Center Coords (ll): {cafe_params.get('ll')}")

    raw_cafes = fetch_restaurants_from_serpapi(cafe_params)
    cafes = normalize_restaurants_response(raw_cafes, limit=3)
    print(f"  * Retrieved {len(cafes)} live normalized cafes:")
    for c in cafes:
        print(f"    - {c.name} | Rating: {c.rating} | Category: {c.category} | Coords: ({c.latitude}, {c.longitude})")
        assert c.name, "Cafe name cannot be empty"
        assert c.data_id, "Cafe data_id cannot be empty"

    assert len(cafes) > 0, "Expected at least 1 cafe from live search"

    # 3. Live Route Optimization Integrating Hotel + Place + Restaurant
    print("\n[3/3] Testing Live Route Optimization (Hotel -> Attraction -> Restaurant)...")
    chosen_rest = restaurants[0]
    stops = [
        RouteStop(id="place_1", name="Fort Kochi", latitude=9.9658, longitude=76.2421, type="attraction"),
        RouteStop(
            id="rest_1",
            name=chosen_rest.name,
            latitude=chosen_rest.latitude or 9.9880,
            longitude=chosen_rest.longitude or 76.2625,
            type="restaurant",
            meal_type="lunch",
        ),
    ]
    route_req = RouteRequest(
        start_location=LocationPoint(name="Grand Hyatt Kochi Bolgatty", latitude=9.9880, longitude=76.2625),
        stops=stops,
        mode="driving",
        respect_user_order=False,
    )
    opt_route = optimize_route(route_req, api_key=api_key)
    print(f"  * Optimized Order: {[s.name for s in opt_route.ordered_stops]}")
    print(f"  * Total Distance: {opt_route.total_distance_meters / 1000:.2f} km")
    print(f"  * Total Duration: {opt_route.total_duration_seconds / 60:.1f} mins")
    assert len(opt_route.ordered_stops) == 2, "Expected 2 ordered stops"
    assert opt_route.total_distance_meters > 0, "Expected positive distance"

    print("\n" + "=" * 65)
    print("LIVE SERPAPI VALIDATION PASSED 100%!")
    print("=" * 65)


if __name__ == "__main__":
    test_live_serpapi()
