"""
Project Musafir — Live SerpApi Milestone 1 Verification
Tests live external provider:
1. Live Hotel Search (Kochi, Kerala)
2. Extract coordinates & hotel name
3. Live Anchored Place Discovery around hotel coordinates
4. Live Route Optimization from hotel to 2 discovered places
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

from app.schemas.route import LocationPoint, RouteStop, RouteRequest
from app.services.hotel_service import (
    build_hotel_search_params,
    fetch_hotels_from_serpapi,
    normalize_hotels_response,
)
from app.services.place_service import (
    build_place_search_params,
    fetch_places_from_serpapi,
    normalize_places_response,
)
from app.services.route_service import optimize_route

api_key = os.getenv("SERPAPI_API_KEY")
print("=" * 60)
print("PROJECT MUSAFIR — LIVE SERPAPI MILESTONE 1 VALIDATION")
print("=" * 60)

# 1. LIVE HOTEL SEARCH
print("\n[1/3] Live Hotel Search (Kochi, Kerala)...")
from datetime import date

h_params = build_hotel_search_params(
    destination="Kochi, Kerala",
    check_in=date(2026, 9, 24),
    check_out=date(2026, 9, 28),
    adults=2,
    children=0,
    api_key=api_key,
)
h_raw = fetch_hotels_from_serpapi(h_params)
hotels = normalize_hotels_response(h_raw)[:3]
assert len(hotels) > 0, "No hotels returned from live SerpApi"
selected_hotel = hotels[0]
print(f"  ✓ Live Hotel Found: {selected_hotel.name}")
print(f"    Coordinates: {selected_hotel.latitude}, {selected_hotel.longitude}")
print(f"    Price: {selected_hotel.price_per_night}")

# 2. LIVE ANCHORED PLACE SEARCH
print("\n[2/3] Live Anchored Places Discovery around Hotel...")
p_params = build_place_search_params(
    destination="Kochi, Kerala",
    query=f"tourist attractions near {selected_hotel.name}",
    api_key=api_key,
    location_anchor=selected_hotel.name,
    latitude=selected_hotel.latitude,
    longitude=selected_hotel.longitude,
)
p_raw = fetch_places_from_serpapi(p_params)
places = normalize_places_response(p_raw, limit=3)
assert len(places) >= 2, f"Expected at least 2 places, got {len(places)}"
for i, p in enumerate(places, 1):
    print(f"  ✓ Place {i}: {p.name} (lat: {p.latitude}, lng: {p.longitude}, rating: {p.rating})")

# 3. LIVE ROUTE OPTIMIZATION
print("\n[3/3] Live Route Optimization Engine...")
hotel_lat = selected_hotel.latitude or places[0].latitude
hotel_lng = selected_hotel.longitude or places[0].longitude

stops = [
    RouteStop(id=f"stop_{i}", name=p.name, latitude=p.latitude, longitude=p.longitude, type="attraction")
    for i, p in enumerate(places[:2], 1)
]
route_req = RouteRequest(
    start_location=LocationPoint(name=selected_hotel.name, latitude=hotel_lat, longitude=hotel_lng),
    stops=stops,
    mode="driving",
    respect_user_order=False,
)
opt_route = optimize_route(route_req, api_key=api_key)
assert opt_route.total_distance_meters > 0, "Invalid route distance"
print(f"  ✓ Live Route Successfully Optimized!")
print(f"    Origin: {selected_hotel.name}")
print(f"    Stops: {[s.name for s in opt_route.ordered_stops]}")
print(f"    Total Distance: {opt_route.total_distance_meters/1000:.2f} km")
print(f"    Total Travel Time: {opt_route.total_duration_seconds/60:.1f} mins")

print("\n" + "=" * 60)
print("LIVE SERPAPI MILESTONE 1 VALIDATION PASSED 100%!")
print("=" * 60)
