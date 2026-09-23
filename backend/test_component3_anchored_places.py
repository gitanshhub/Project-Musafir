"""
Project Musafir — Component 3 Verification: Anchored Place Discovery & Route Direct Execution
Verifies:
1. build_place_search_params accepts location_anchor
2. coordinates are supported
3. anchor creates "attractions near <hotel>"
4. coordinates create Google Maps ll parameter
5. search_places_tool accepts anchor/lat/lng
6. hotel becomes default anchor for "near hotel"
7. rejected_places are excluded
8. optimize_route_tool can build its inputs from TripState
9. hotel_selection is used as route origin
10. selected_places become route stops
11. no manual coordinate input required for state-driven route call
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from unittest.mock import patch
from app.services.place_service import build_place_search_params, normalize_place
from app.agent.state import TripState, PlanningStage
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.route import OptimizedRoute, RouteStop, RouteSegment
from app.agent.tools import search_places_tool, optimize_route_tool, execute_tool

print("=" * 60)
print("PROJECT MUSAFIR — COMPONENT 3 ANCHORED DISCOVERY VERIFICATION")
print("=" * 60)

# TEST 1: build_place_search_params with location_anchor & coordinates
print("\n[Test 1] Testing build_place_search_params with location_anchor & coordinates...")
params1 = build_place_search_params(
    destination="Jaipur",
    location_anchor="Fairmont Jaipur",
    latitude=26.9855,
    longitude=75.8513,
    api_key="mock_key",
)
assert params1["q"] == "attractions near Fairmont Jaipur, Jaipur", f"Unexpected q: {params1['q']}"
assert params1["ll"] == "@26.9855,75.8513,15z", f"Unexpected ll: {params1.get('ll')}"
assert params1["api_key"] == "mock_key"
print(f"  ✓ Anchor query: {params1['q']}")
print(f"  ✓ Coordinates ll: {params1['ll']}")
print("  ✓ Test 1 Passed!")

# TEST 2: Custom query with location_anchor
print("\n[Test 2] Testing build_place_search_params with custom query...")
params2 = build_place_search_params(
    destination="Jaipur",
    query="forts and viewpoints",
    location_anchor="Fairmont Jaipur",
    api_key="mock_key",
)
assert "forts and viewpoints near Fairmont Jaipur" in params2["q"]
assert "ll" not in params2, "ll should not be present when coords are None"
print(f"  ✓ Query with anchor: {params2['q']}")
print("  ✓ Test 2 Passed!")

# TEST 3: Backward compatibility (no anchor)
print("\n[Test 3] Testing build_place_search_params backward compatibility...")
params3 = build_place_search_params(
    destination="Jaipur",
    category="historical",
    query="palaces",
    api_key="mock_key",
)
assert params3["q"] == "Jaipur palaces historical"
assert "ll" not in params3
print(f"  ✓ Unanchored query: {params3['q']}")
print("  ✓ Test 3 Passed!")

# TEST 4: search_places_tool with anchor, lat, lng and rejected_places filtering
print("\n[Test 4] Testing search_places_tool with rejection filtering...")
mock_serpapi_response = {
    "local_results": [
        {
            "title": "Amber Fort",
            "data_id": "p_amber",
            "rating": 4.7,
            "gps_coordinates": {"latitude": 26.9855, "longitude": 75.8513},
        },
        {
            "title": "Jaigarh Fort",
            "data_id": "p_jaigarh",
            "rating": 4.5,
            "gps_coordinates": {"latitude": 26.9850, "longitude": 75.8450},
        },
        {
            "title": "Nahargarh Fort",
            "data_id": "p_nahargarh",
            "rating": 4.6,
            "gps_coordinates": {"latitude": 26.9370, "longitude": 75.8150},
        },
    ]
}

with patch("app.agent.tools.fetch_places_from_serpapi", return_value=mock_serpapi_response):
    # Exclude Jaigarh Fort
    res = search_places_tool(
        destination="Jaipur",
        location_anchor="Fairmont Jaipur",
        latitude=26.9855,
        longitude=75.8513,
        exclude_names=["Jaigarh Fort"],
        api_key="mock_key",
    )
    assert res["success"] is True
    place_names = [p["name"] for p in res["places"]]
    assert "Amber Fort" in place_names
    assert "Nahargarh Fort" in place_names
    assert "Jaigarh Fort" not in place_names, "Jaigarh Fort should be excluded!"
    print(f"  ✓ Returned non-excluded places: {place_names}")
    print("  ✓ Test 4 Passed!")

# TEST 5: search_places_tool inherits hotel anchor and rejected_places from TripState
print("\n[Test 5] Testing search_places_tool inheriting hotel anchor and rejected places from TripState...")
hotel = Hotel(name="Fairmont Jaipur", latitude=26.9855, longitude=75.8513)
state = TripState(destination="Jaipur", selected_hotel=hotel)
state.reject_place("Nahargarh Fort")

with patch("app.agent.tools.fetch_places_from_serpapi", return_value=mock_serpapi_response) as mock_fetch:
    res_state = search_places_tool(
        destination="Jaipur",
        query="places near the hotel",
        trip_state=state,
        api_key="mock_key",
    )
    assert res_state["success"] is True
    # Verify build_place_search_params received hotel anchor
    called_params = mock_fetch.call_args[0][0]
    assert "Fairmont Jaipur" in called_params["q"], f"Expected hotel anchor in q: {called_params['q']}"
    assert called_params["ll"] == "@26.9855,75.8513,15z"
    # Verify rejection from state was applied
    places_returned = [p["name"] for p in res_state["places"]]
    assert "Nahargarh Fort" not in places_returned, "Nahargarh Fort in rejected_places must be filtered out"
    print(f"  ✓ Automatically anchored query: {called_params['q']}")
    print(f"  ✓ Filtered out state rejected place: {places_returned}")
    print("  ✓ Test 5 Passed!")

# TEST 6: optimize_route_tool hydrated directly from TripState
print("\n[Test 6] Testing optimize_route_tool hydrated directly from TripState...")
state.add_place(Place(data_id="p1", name="Amber Fort", latitude=26.9855, longitude=75.8513))
state.add_place(Place(data_id="p2", name="Hawa Mahal", latitude=26.9239, longitude=75.8267))

mock_optimized_route = OptimizedRoute(
    ordered_stops=[
        RouteStop(id="p1", name="Amber Fort", latitude=26.9855, longitude=75.8513, type="attraction"),
        RouteStop(id="p2", name="Hawa Mahal", latitude=26.9239, longitude=75.8267, type="attraction"),
    ],
    segments=[
        RouteSegment(from_stop="Amber Fort", to_stop="Hawa Mahal", distance_meters=8500, duration_seconds=1200)
    ],
    total_distance_meters=8500,
    total_duration_seconds=1200,
    score=95.0,
)

with patch("app.agent.tools.service_optimize_route", return_value=mock_optimized_route) as mock_route_svc:
    # No manual start_location, no manual stops passed; only trip_state
    route_res = optimize_route_tool(trip_state=state, api_key="mock_key")
    assert route_res["success"] is True, f"Failed: {route_res.get('error')}"
    req = mock_route_svc.call_args[0][0]
    # Check origin matches hotel
    assert req.start_location.name == "Fairmont Jaipur"
    assert req.start_location.latitude == 26.9855
    # Check stops match selected places
    assert len(req.stops) == 2
    assert req.stops[0].name == "Amber Fort"
    assert req.stops[1].name == "Hawa Mahal"
    print(f"  ✓ Origin automatically resolved to hotel: {req.start_location.name}")
    print(f"  ✓ Stops automatically resolved to selected places: {[s.name for s in req.stops]}")
    print("  ✓ Test 6 Passed!")

# TEST 7: execute_tool dispatcher injects trip_state into optimize_route
print("\n[Test 7] Testing execute_tool injects trip_state into optimize_route...")
with patch("app.agent.tools.service_optimize_route", return_value=mock_optimized_route) as mock_route_svc2:
    dispatch_res = execute_tool(
        name="optimize_route",
        arguments={},  # Empty arguments, relying on trip_state injection
        api_key="mock_key",
        trip_state=state,
    )
    assert dispatch_res["success"] is True, f"Failed: {dispatch_res.get('error')}"
    print("  ✓ execute_tool successfully auto-hydrated route from state.")
    print("  ✓ Test 7 Passed!")

print("\n" + "=" * 60)
print("ALL COMPONENT 3 ANCHORED DISCOVERY TESTS PASSED CLEANLY!")
print("=" * 60)
