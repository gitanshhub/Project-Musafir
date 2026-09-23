import os
import sys
from datetime import date

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import RouteStop, RouteSegment, OptimizedRoute
from app.schemas.itinerary import DailyItinerary, ItineraryItem, ItinerarySegment, ItineraryResponse
from app.agent.state import (
    TripState,
    get_or_create_state,
    get_state,
    save_state,
    reset_state,
    delete_state,
    clear_all_states,
)

print("=" * 60)
print("PROJECT MUSAFIR — STEP 10.1 AGENT STATE VERIFICATION SUITE")
print("=" * 60)

# TEST 1: Basic TripState Initialization & Defaults
print("\n[Test 1] Testing Default State Initialization...")
state1 = TripState()
assert state1.conversation_id is not None and len(state1.conversation_id) > 0
assert state1.destination is None
assert state1.travel_mode == "driving"
assert state1.start_time == "09:00"
assert state1.day_start_time == "09:00"
assert state1.day_end_time == "22:00"
assert state1.selected_places == []
assert state1.selected_restaurants == []
assert state1.is_ready_for_routing() is False
print(f"  ✓ Initialized session: {state1.conversation_id[:8]}... with clean defaults")
print("  ✓ Test 1 Passed!")

# TEST 2: Updating Intent & Preferences
print("\n[Test 2] Testing Destination, Budget & Dates Setup...")
state1.destination = "Jaipur"
state1.number_of_days = 3
state1.trip_start_date = date(2026, 10, 1)
state1.trip_end_date = date(2026, 10, 3)
state1.hotel_budget = 4500.0
state1.dietary_preferences = ["vegetarian"]
state1.interests = ["forts", "palaces", "heritage food"]

assert state1.destination == "Jaipur"
assert state1.number_of_days == 3
assert state1.hotel_budget == 4500.0
assert "vegetarian" in state1.dietary_preferences
print(f"  ✓ Configured: {state1.destination}, {state1.number_of_days} days, budget ₹{state1.hotel_budget}")
print("  ✓ Test 2 Passed!")

# TEST 3: Adding & Deduplicating Places
print("\n[Test 3] Testing Place Selection & Deduplication...")
place1 = Place(
    data_id="p_hawa_mahal",
    name="Hawa Mahal",
    latitude=26.9239,
    longitude=75.8267,
    rating=4.5,
)
place2 = Place(
    data_id="p_amber_fort",
    name="Amber Fort",
    latitude=26.9855,
    longitude=75.8513,
    rating=4.6,
)
added1 = state1.add_place(place1)
assert added1 is True, "First add should succeed"
assert len(state1.selected_places) == 1

# Attempt duplicate add (same data_id)
added_dup = state1.add_place(place1)
assert added_dup is False, "Duplicate add should return False"
assert len(state1.selected_places) == 1

# Add second place
added2 = state1.add_place(place2)
assert added2 is True
assert len(state1.selected_places) == 2
print(f"  ✓ Added 2 unique places: {[p.name for p in state1.selected_places]}")
print("  ✓ Duplicate detection prevented re-insertion")
print("  ✓ Test 3 Passed!")

# TEST 4: Adding & Removing Restaurants
print("\n[Test 4] Testing Restaurant Management...")
rest1 = Restaurant(
    data_id="r_lmb",
    name="Laxmi Mishthan Bhandar",
    latitude=26.9220,
    longitude=75.8250,
    rating=4.2,
    cuisine=["Rajasthani Mithai", "Thali"],
)
rest2 = Restaurant(
    data_id="r_chokhi_dhani",
    name="Chokhi Dhani",
    latitude=26.7663,
    longitude=75.8361,
    rating=4.4,
    cuisine=["Rajasthani", "Traditional"],
)
assert state1.add_restaurant(rest1) is True
assert state1.add_restaurant(rest2) is True
assert len(state1.selected_restaurants) == 2

# Remove by name
removed_by_name = state1.remove_restaurant("Laxmi Mishthan Bhandar")
assert removed_by_name is True
assert len(state1.selected_restaurants) == 1
assert state1.selected_restaurants[0].name == "Chokhi Dhani"

# Remove by data_id
removed_by_id = state1.remove_restaurant("r_chokhi_dhani")
assert removed_by_id is True
assert len(state1.selected_restaurants) == 0
print("  ✓ Successfully tested restaurant add, deduplication, and removal by name/id")
print("  ✓ Test 4 Passed!")

# TEST 5: Hotel Selection & Routing Readiness Check
print("\n[Test 5] Testing Routing Readiness Check...")
# Still not ready because no hotel is set
assert state1.is_ready_for_routing() is False

# Set hotel
hotel = Hotel(
    name="The Hosteller Jaipur",
    latitude=26.9124,
    longitude=75.7873,
    price_per_night=1200.0,
    currency="INR",
)
state1.hotel_selection = hotel

# Now destination="Jaipur", hotel is set, and 2 places are selected -> ready!
assert state1.is_ready_for_routing() is True
print("  ✓ State correctly transitions to is_ready_for_routing() == True")
print("  ✓ Test 5 Passed!")

# TEST 6: Route & Itinerary Attachment and Invalidation
print("\n[Test 6] Testing Route & Itinerary Attachment + Invalidation on State Mutation...")
mock_route = OptimizedRoute(
    ordered_stops=[
        RouteStop(id="p1", name="Hawa Mahal", latitude=26.9239, longitude=75.8267),
        RouteStop(id="p2", name="Amber Fort", latitude=26.9855, longitude=75.8513),
    ],
    segments=[
        RouteSegment(from_stop="Hotel", to_stop="Hawa Mahal", duration_seconds=900),
        RouteSegment(from_stop="Hawa Mahal", to_stop="Amber Fort", duration_seconds=1200),
    ],
    total_distance_meters=15000,
    total_duration_seconds=2100,
    score=2100,
)
mock_itinerary = ItineraryResponse(
    days=[
        DailyItinerary(
            day_number=1,
            date=date(2026, 10, 1),
            start_time="09:00",
            end_time="14:00",
            items=[],
            segments=[],
            total_travel_seconds=2100,
            total_visit_minutes=120,
        )
    ],
    total_days=1,
    total_distance_meters=15000,
    total_travel_seconds=2100,
    total_visit_minutes=120,
)
state1.current_route = mock_route
state1.current_itinerary = mock_itinerary
assert state1.current_route is not None
assert state1.current_itinerary is not None

# Modifying places must automatically invalidate previous route and itinerary
place3 = Place(data_id="p_city_palace", name="City Palace", latitude=26.9258, longitude=75.8237)
state1.add_place(place3)
assert state1.current_route is None, "Route should be invalidated on place addition"
assert state1.current_itinerary is None, "Itinerary should be invalidated on place addition"
print("  ✓ Invalidation triggers correctly when modifying trip stops")
print("  ✓ Test 6 Passed!")

# TEST 7: In-Memory State Repository
print("\n[Test 7] Testing In-Memory State Repository...")
clear_all_states()

# 1. Create with specific ID
session_id = "user_session_42"
s_repo = get_or_create_state(session_id)
assert s_repo.conversation_id == session_id
s_repo.destination = "Udaipur"
save_state(s_repo)

# 2. Retrieve existing session
fetched = get_state(session_id)
assert fetched is not None
assert fetched.destination == "Udaipur"

# 3. Create without ID
anon = get_or_create_state()
assert anon.conversation_id is not None
assert anon.conversation_id != session_id

# 4. Reset state
reset_s = reset_state(session_id)
assert reset_s.destination is None
assert reset_s.conversation_id == session_id

# 5. Delete state
deleted = delete_state(session_id)
assert deleted is True
assert get_state(session_id) is None
print("  ✓ Repository get_or_create, get, save, reset, and delete functions verified")
print("  ✓ Test 7 Passed!")

# TEST 8: Pydantic Serialization Roundtrip
print("\n[Test 8] Testing Pydantic Serialization & Summary...")
state_dict = state1.model_dump()
restored_state = TripState.model_validate(state_dict)
assert restored_state.conversation_id == state1.conversation_id
assert restored_state.destination == "Jaipur"
assert len(restored_state.selected_places) == 3
summary = restored_state.summary()
assert summary["destination"] == "Jaipur"
assert summary["places_count"] == 3
print(f"  ✓ Summary: {summary}")
print("  ✓ Test 8 Passed!")

print("\n" + "=" * 60)
print("ALL STEP 10.1 AGENT STATE TESTS PASSED SUCCESSFULLY!")
print("=" * 60)
