import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

from app.agent.tools import (
    search_hotels_tool,
    get_hotel_details_tool,
    search_places_tool,
    search_restaurants_tool,
    optimize_route_tool,
    generate_itinerary_tool,
    execute_tool,
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
)

api_key = os.getenv("SERPAPI_API_KEY")

print("=" * 60)
print("PROJECT MUSAFIR — STEP 10.2 AGENT TOOL LAYER VERIFICATION SUITE")
print("=" * 60)

# TEST 1: Tool Definitions & Schemas
print("\n[Test 1] Validating Tool Definitions & OpenAI Function Calling Schemas...")
assert len(TOOL_DEFINITIONS) >= 6, f"Expected at least 6 tools, got {len(TOOL_DEFINITIONS)}"
tool_names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
expected_names = [
    "update_trip_state",
    "search_hotels",
    "get_hotel_details",
    "search_places",
    "search_restaurants",
    "optimize_route",
    "generate_itinerary",
]
assert all(name in tool_names for name in expected_names), f"Tool names mismatch: {tool_names}"

for t in TOOL_DEFINITIONS:
    assert t["type"] == "function"
    fn = t["function"]
    assert "name" in fn and len(fn["name"]) > 0
    assert "description" in fn and len(fn["description"]) > 0
    assert "parameters" in fn
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "properties" in params
    assert "required" in params
    print(f"  ✓ Validated schema: {fn['name']} ({len(params['properties'])} properties)")

print("  ✓ Test 1 Passed!")


# TEST 2: search_hotels_tool
print("\n[Test 2] Testing search_hotels_tool...")
tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
day_after = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")

h_result = search_hotels_tool(
    destination="Jaipur",
    check_in=tomorrow,
    check_out=day_after,
    adults=2,
    max_price=5000.0,
    limit=3,
    api_key=api_key,
)
assert h_result["success"] is True, f"search_hotels failed: {h_result.get('error')}"
assert "hotels" in h_result and len(h_result["hotels"]) > 0
print(f"  ✓ Found {len(h_result['hotels'])} hotels under ₹5000/night:")
for h in h_result["hotels"]:
    print(f"    * {h['name']} — ₹{h.get('price_per_night')} (Rating: {h.get('rating')})")
print("  ✓ Test 2 Passed!")


# TEST 3: get_hotel_details_tool
print("\n[Test 3] Testing get_hotel_details_tool...")
first_hotel = h_result["hotels"][0]
prop_token = first_hotel.get("property_token")
if prop_token:
    d_result = get_hotel_details_tool(
        property_token=prop_token,
        check_in=tomorrow,
        check_out=day_after,
        adults=2,
        api_key=api_key,
    )
    assert d_result["success"] is True, f"get_hotel_details failed: {d_result.get('error')}"
    hotel_info = d_result["hotel"]
    print(f"  ✓ Retrieved details for: {hotel_info['name']}")
    print(f"    * Address: {hotel_info.get('address')}")
    print(f"    * Amenities: {len(hotel_info.get('amenities', []))} amenities")
    print(f"    * Photos: {len(hotel_info.get('images', []))} images")
else:
    print("  ⚠ No property_token returned on first hotel; testing error handling on missing token")
    err_test = get_hotel_details_tool(property_token="", api_key=api_key)
    assert err_test["success"] is False
print("  ✓ Test 3 Passed!")


# TEST 4: search_places_tool
print("\n[Test 4] Testing search_places_tool...")
p_result = search_places_tool(
    destination="Jaipur",
    category="historical",
    query="forts",
    limit=3,
    api_key=api_key,
)
assert p_result["success"] is True, f"search_places failed: {p_result.get('error')}"
assert len(p_result["places"]) > 0
print(f"  ✓ Found {len(p_result['places'])} historical forts:")
for p in p_result["places"]:
    print(f"    * {p['name']} (Rating: {p.get('rating')}, Coords: {p.get('latitude')},{p.get('longitude')})")
print("  ✓ Test 4 Passed!")


# TEST 5: search_restaurants_tool
print("\n[Test 5] Testing search_restaurants_tool...")
r_result = search_restaurants_tool(
    destination="Jaipur",
    query="Rajasthani thali",
    limit=3,
    api_key=api_key,
)
assert r_result["success"] is True, f"search_restaurants failed: {r_result.get('error')}"
assert len(r_result["restaurants"]) > 0
print(f"  ✓ Found {len(r_result['restaurants'])} restaurants:")
for r in r_result["restaurants"]:
    print(f"    * {r['name']} (Rating: {r.get('rating')}, Cuisine: {r.get('cuisine')})")
print("  ✓ Test 5 Passed!")


# TEST 6: optimize_route_tool
print("\n[Test 6] Testing optimize_route_tool...")
selected_places = p_result["places"][:2]
selected_rest = r_result["restaurants"][0]

stops_payload = [
    {
        "id": f"place_{i}",
        "name": p["name"],
        "latitude": p["latitude"],
        "longitude": p["longitude"],
        "type": "attraction",
    }
    for i, p in enumerate(selected_places)
]
stops_payload.append(
    {
        "id": "lunch_stop",
        "name": selected_rest["name"],
        "latitude": selected_rest["latitude"],
        "longitude": selected_rest["longitude"],
        "type": "restaurant",
        "meal_type": "lunch",
    }
)

start_loc = {
    "name": first_hotel["name"],
    "latitude": first_hotel["latitude"] or 26.9124,
    "longitude": first_hotel["longitude"] or 75.7873,
}

route_res = optimize_route_tool(
    start_location=start_loc,
    stops=stops_payload,
    mode="driving",
    respect_user_order=False,
    api_key=api_key,
)
assert route_res["success"] is True, f"optimize_route failed: {route_res.get('error')}"
route_data = route_res["route"]
print(f"  ✓ Optimized stop sequence: {[s['name'] for s in route_data['ordered_stops']]}")
print(f"  ✓ Total distance: {route_data['total_distance_meters']/1000:.2f} km")
print(f"  ✓ Total duration: {route_data['total_duration_seconds']/60:.1f} mins")
print("  ✓ Test 6 Passed!")


# TEST 7: generate_itinerary_tool
print("\n[Test 7] Testing generate_itinerary_tool...")
itin_res = generate_itinerary_tool(
    route=route_data,
    trip_date=tomorrow,
    start_time="09:00",
    day_start_time="09:00",
    day_end_time="22:00",
)
assert itin_res["success"] is True, f"generate_itinerary failed: {itin_res.get('error')}"
itin_data = itin_res["itinerary"]
assert itin_data["total_days"] >= 1
day1 = itin_data["days"][0]
print(f"  ✓ Generated timetable for Day 1 ({day1['date']}): {day1['start_time']} to {day1['end_time']}")
for seg, item in zip(day1["segments"], day1["items"]):
    print(f"    * Travel: {seg['from_stop']} -> {seg['to_stop']} ({seg['departure_time']} - {seg['arrival_time']})")
    print(f"    * Visit:  {item['name']} ({item['arrival_time']} - {item['departure_time']}) [{item['duration_minutes']} min]")
print("  ✓ Test 7 Passed!")


# TEST 8: Central Dispatcher execute_tool
print("\n[Test 8] Testing Central Tool Dispatcher (execute_tool)...")
# 1. Valid dispatch
exec_res = execute_tool("search_places", {"destination": "Jaipur", "limit": 2}, api_key=api_key)
assert exec_res["success"] is True
assert len(exec_res["places"]) == 2
print("  ✓ Dispatched 'search_places' via execute_tool successfully")

# 2. Unknown tool
unknown_res = execute_tool("book_flight", {"destination": "Jaipur"})
assert unknown_res["success"] is False
assert "not recognized" in unknown_res["error"]
print(f"  ✓ Clean rejection of unknown tool: {unknown_res['error']}")

# 3. Invalid arguments
bad_args_res = execute_tool("search_places", {"destination": ""}, api_key=api_key)
assert bad_args_res["success"] is False
print(f"  ✓ Clean error on invalid arguments: {bad_args_res['error']}")

print("  ✓ Test 8 Passed!")

print("\n" + "=" * 60)
print("ALL STEP 10.2 AGENT TOOL LAYER TESTS PASSED SUCCESSFULLY!")
print("=" * 60)
