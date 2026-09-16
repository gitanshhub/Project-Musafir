import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load env before importing app
load_dotenv()

from app.schemas.route import LocationPoint, RouteStop, RouteRequest
from app.services.hotel_service import (
    build_hotel_search_params,
    fetch_hotels_from_serpapi as fetch_hotels,
    normalize_hotels_response,
    build_hotel_detail_params,
    normalize_hotel_detail,
)
from app.services.place_service import (
    build_place_search_params,
    fetch_places_from_serpapi as fetch_places,
    normalize_places_response,
)
from app.services.restaurant_service import (
    build_restaurant_search_params,
    fetch_restaurants_from_serpapi as fetch_restaurants,
    normalize_restaurants_response,
)
from app.services.direction_service import (
    build_direction_params,
    fetch_directions_from_serpapi as fetch_directions,
    normalize_direction_response,
)
from app.services.route_service import optimize_route

api_key = os.getenv("SERPAPI_API_KEY")
print("=" * 60)
print("PROJECT MUSAFIR — LIVE BACKEND VERIFICATION SUITE")
print("=" * 60)

# 1. TEST PLACES (Jaipur historical places)
print("\n[1/4] Testing Places Discovery (Jaipur)...")
p_params = build_place_search_params(destination="Jaipur", category="historical", query=None, api_key=api_key)
p_raw = fetch_places(p_params)
places = normalize_places_response(p_raw, limit=3)
for p in places:
    print(f"  * {p.name} (Rating: {p.rating}, Lat/Lng: {p.latitude},{p.longitude})")
assert len(places) > 0, "No places returned"

# 2. TEST RESTAURANTS (Jaipur Rajasthani food)
print("\n[2/4] Testing Restaurant Discovery (Jaipur)...")
r_params = build_restaurant_search_params(destination="Jaipur", category=None, query="Rajasthani food", api_key=api_key)
r_raw = fetch_restaurants(r_params)
restaurants = normalize_restaurants_response(r_raw, limit=3)
for r in restaurants:
    print(f"  * {r.name} (Rating: {r.rating}, Price: {r.price_level}, Lat/Lng: {r.latitude},{r.longitude})")
assert len(restaurants) > 0, "No restaurants returned"

# 3. TEST DIRECTIONS
print("\n[3/4] Testing Point-to-Point Directions...")
start_coords = f"{places[0].latitude},{places[0].longitude}"
dest_coords = f"{places[1].latitude},{places[1].longitude}"
d_params = build_direction_params(origin=start_coords, destination=dest_coords, mode="driving", api_key=api_key)
d_raw = fetch_directions(d_params)
direction = normalize_direction_response(d_raw, origin=start_coords, destination=dest_coords, mode="driving")
print(f"  * {places[0].name} -> {places[1].name}: {direction.distance_text}, {direction.duration_text}")
assert direction.distance_meters > 0, "Invalid distance"

# 4. TEST ROUTE OPTIMIZATION
print("\n[4/4] Testing Route Optimization Engine...")
stops = [
    RouteStop(id=f"place_{i}", name=p.name, latitude=p.latitude, longitude=p.longitude, type="attraction")
    for i, p in enumerate(places[:2])
]
stops.append(
    RouteStop(id="dinner", name=restaurants[0].name, latitude=restaurants[0].latitude, longitude=restaurants[0].longitude, type="restaurant", latest_visit="night")
)
route_req = RouteRequest(
    start_location=LocationPoint(name="Jaipur Hotel", latitude=26.9124, longitude=75.7873),
    stops=stops,
    mode="driving",
    respect_user_order=False,
)
opt_route = optimize_route(route_req, api_key=api_key)
print(f"  * Optimized Visit Sequence: {[s.name for s in opt_route.ordered_stops]}")
print(f"  * Total Distance: {opt_route.total_distance_meters/1000:.2f} km")
print(f"  * Total Driving Time: {opt_route.total_duration_seconds/60:.1f} mins")
print(f"  * Optimization Reason: {opt_route.reason}")
for i, seg in enumerate(opt_route.segments, 1):
    print(f"    Leg {i}: {seg.from_stop} -> {seg.to_stop} ({seg.distance_text}, {seg.duration_text})")

# 5. TEST ITINERARY ENGINE (Chain Step 8 -> Step 9)
from app.schemas.itinerary import ItineraryRequest
from app.services.itinerary_service import generate_itinerary

print("\n[5/5] Testing Itinerary Generation Engine...")
itin_req = ItineraryRequest(
    route=opt_route,
    trip_date=date.today() + timedelta(days=1),
    start_time="09:30",
    day_start_time="09:00",
    day_end_time="22:00",
)
itin_res = generate_itinerary(itin_req)
print(f"  * Total Days: {itin_res.total_days}")
print(f"  * Total Trip Distance: {itin_res.total_distance_meters / 1000:.2f} km")
print(f"  * Total Trip Travel Time: {itin_res.total_travel_seconds / 60:.1f} mins")
print(f"  * Total Activities Visit Time: {itin_res.total_visit_minutes} mins")
for d in itin_res.days:
    print(f"\n  --- Day {d.day_number} ({d.date}): {d.start_time} to {d.end_time} ---")
    for s, item in zip(d.segments, d.items):
        print(f"    Travel: {s.from_stop} -> {s.to_stop} ({s.departure_time} - {s.arrival_time}) [{s.duration_text or f'{s.duration_seconds/60:.0f} mins'}]")
        print(f"    Visit:  {item.name} ({item.arrival_time} - {item.departure_time}) [{item.duration_minutes} mins]")

assert itin_res.total_days >= 1
assert len(itin_res.days[0].items) > 0

print("\n" + "=" * 60)
print("ALL LIVE VERIFICATION TESTS PASSED SUCCESSFULLY!")
print("=" * 60)
