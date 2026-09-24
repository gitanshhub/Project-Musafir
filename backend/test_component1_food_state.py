"""
Project Musafir — Milestone 2 Component 1 Verification: Food Planning State Model
Verifies:
1. Food state fields exist with proper defaults (selected_restaurants, selected_cafes, rejected_restaurants, rejected_cafes, preferences).
2. Food state serializes and deserializes cleanly via Pydantic model_dump and JSON.
3. Restaurant can be selected, deduplicated, and removed.
4. Café can be selected, deduplicated, and removed.
5. Unified properties (selected_food, rejected_food) work accurately.
6. Food can be rejected and is purged from active selections.
7. Food additions/removals/rejections do not corrupt selected_places or hotel_selection.
8. Routing readiness (is_ready_for_routing) accurately accounts for food stops.
9. apply_trip_state_update mutates food fields and transitions stage to FOOD_SELECTION.
10. summary() includes cafes_count, cafes, rejected_restaurants, rejected_cafes, rejected_food, cuisine_preferences, food_price_preference.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import json
from app.agent.state import TripState, PlanningStage, clear_all_states
from app.agent.state_update import apply_trip_state_update
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant

print("=" * 60)
print("PROJECT MUSAFIR — MILESTONE 2 COMPONENT 1: FOOD STATE MODEL")
print("=" * 60)

# TEST 1: Food State Defaults & Fields
print("\n[Test 1] Testing Food State Fields & Defaults...")
st = TripState()
assert st.selected_restaurants == []
assert st.selected_cafes == []
assert st.rejected_restaurants == []
assert st.rejected_cafes == []
assert st.dietary_preferences == []
assert st.meal_preferences == {}
assert st.cuisine_preferences == []
assert st.food_price_preference is None
assert st.selected_food == []
assert st.rejected_food == []
assert PlanningStage.FOOD_DISCOVERY == "FOOD_DISCOVERY"
assert PlanningStage.FOOD_SELECTION == "FOOD_SELECTION"
print("  ✓ All food state fields and PlanningStage enum verified with clean defaults")
print("  ✓ Test 1 Passed!")

# TEST 2: Serialization & Deserialization
print("\n[Test 2] Testing Pydantic Serialization & Deserialization...")
st.destination = "Kochi"
st.dietary_preferences = ["vegetarian", "jain"]
st.cuisine_preferences = ["South Indian", "Kerala Traditional"]
st.food_price_preference = "cheap"
st.meal_preferences = {"lunch": "banana leaf thali", "dinner": "seafood"}

r1 = Restaurant(
    data_id="rest_1",
    name="Dhe Puttu",
    latitude=9.9812,
    longitude=76.2845,
    rating=4.5,
    cuisine=["Kerala", "Breakfast"],
    price_level="₹₹",
)
c1 = Restaurant(
    data_id="cafe_1",
    name="Kashi Art Cafe",
    latitude=9.9654,
    longitude=76.2412,
    rating=4.6,
    cuisine=["Cafe", "Continental"],
    price_level="₹₹",
)
st.add_restaurant(r1)
st.add_cafe(c1)

dumped = st.model_dump()
assert len(dumped["selected_restaurants"]) == 1
assert len(dumped["selected_cafes"]) == 1
assert dumped["selected_restaurants"][0]["name"] == "Dhe Puttu"
assert dumped["selected_cafes"][0]["name"] == "Kashi Art Cafe"
assert dumped["cuisine_preferences"] == ["South Indian", "Kerala Traditional"]
assert dumped["food_price_preference"] == "cheap"

json_str = st.model_dump_json()
restored = TripState.model_validate_json(json_str)
assert restored.destination == "Kochi"
assert len(restored.selected_restaurants) == 1
assert len(restored.selected_cafes) == 1
assert restored.selected_restaurants[0].name == "Dhe Puttu"
assert restored.selected_cafes[0].name == "Kashi Art Cafe"
assert restored.cuisine_preferences == ["South Indian", "Kerala Traditional"]
assert restored.food_price_preference == "cheap"
print("  ✓ TripState with restaurants, cafes, and preferences serializes/deserializes cleanly")
print("  ✓ Test 2 Passed!")

# TEST 3: Restaurant Selection & Deduplication
print("\n[Test 3] Testing Restaurant Selection & Deduplication...")
st3 = TripState()
r_a = Restaurant(data_id="r_a", name="Grand Pavillion", latitude=9.9700, longitude=76.2800)
r_b = Restaurant(data_id="r_b", name="Paragon", latitude=9.9800, longitude=76.2900)

assert st3.add_restaurant(r_a) is True
assert st3.add_restaurant(r_a) is False  # Duplicate by data_id
r_a_dup_name = Restaurant(data_id="different_id", name="Grand Pavillion", latitude=9.9700, longitude=76.2800)
assert st3.add_restaurant(r_a_dup_name) is False  # Duplicate by name
assert st3.add_restaurant(r_b) is True
assert len(st3.selected_restaurants) == 2

assert st3.remove_restaurant("Grand Pavillion") is True
assert len(st3.selected_restaurants) == 1
assert st3.selected_restaurants[0].name == "Paragon"
print("  ✓ Restaurant addition, deduplication, and removal working cleanly")
print("  ✓ Test 3 Passed!")

# TEST 4: Café Selection & Deduplication
print("\n[Test 4] Testing Café Selection & Deduplication...")
st4 = TripState()
c_a = Restaurant(data_id="c_a", name="Teapot Cafe", latitude=9.9660, longitude=76.2430)
c_b = Restaurant(data_id="c_b", name="Qissa Cafe", latitude=9.9670, longitude=76.2440)

assert st4.add_cafe(c_a) is True
assert st4.add_cafe(c_a) is False  # Duplicate
assert st4.add_cafe(c_b) is True
assert len(st4.selected_cafes) == 2

assert st4.remove_cafe("Teapot Cafe") is True
assert len(st4.selected_cafes) == 1
assert st4.selected_cafes[0].name == "Qissa Cafe"
print("  ✓ Café addition, deduplication, and removal working cleanly")
print("  ✓ Test 4 Passed!")

# TEST 5: Unified Food Properties
print("\n[Test 5] Testing Unified Food Properties (selected_food, rejected_food)...")
st5 = TripState()
st5.add_restaurant(r_a)
st5.add_cafe(c_a)
assert len(st5.selected_food) == 2
food_names = [f.name for f in st5.selected_food]
assert "Grand Pavillion" in food_names
assert "Teapot Cafe" in food_names

st5.reject_restaurant("Paragon")
st5.reject_cafe("Qissa Cafe")
assert "Paragon" in st5.rejected_food
assert "Qissa Cafe" in st5.rejected_food
print(f"  ✓ Unified selected_food: {food_names}")
print(f"  ✓ Unified rejected_food: {st5.rejected_food}")
print("  ✓ Test 5 Passed!")

# TEST 6: Food Rejection & Purging
print("\n[Test 6] Testing Food Rejection & Purging from Active Selections...")
st6 = TripState()
st6.add_restaurant(r_a)
st6.add_cafe(c_a)
assert len(st6.selected_restaurants) == 1
assert len(st6.selected_cafes) == 1

# Rejecting restaurant removes it from selected_restaurants
assert st6.reject_restaurant("Grand Pavillion") is True
assert len(st6.selected_restaurants) == 0
assert "Grand Pavillion" in st6.rejected_restaurants

# Rejecting cafe removes it from selected_cafes
assert st6.reject_cafe("Teapot Cafe") is True
assert len(st6.selected_cafes) == 0
assert "Teapot Cafe" in st6.rejected_cafes

# Unreject
assert st6.unreject_restaurant("Grand Pavillion") is True
assert "Grand Pavillion" not in st6.rejected_restaurants
print("  ✓ Food rejection purges active selection and records in rejected collections")
print("  ✓ Test 6 Passed!")

# TEST 7: Selection Isolation (Food operations do not corrupt places/hotels)
print("\n[Test 7] Testing Selection Isolation...")
st7 = TripState(destination="Kochi")
hotel = Hotel(name="Grand Hyatt Kochi", latitude=9.9880, longitude=76.2625)
place1 = Place(data_id="p1", name="Fort Kochi", latitude=9.9656, longitude=76.2421)
place2 = Place(data_id="p2", name="Mattancherry Palace", latitude=9.9583, longitude=76.2592)

st7.hotel_selection = hotel
st7.add_place(place1)
st7.add_place(place2)
st7.planning_stage = PlanningStage.PLACE_SELECTION

# Perform food operations
st7.add_restaurant(r_a)
st7.add_cafe(c_a)
st7.reject_restaurant("Some Bad Dhaba")

# Verify places and hotel remain completely pristine
assert st7.hotel_selection.name == "Grand Hyatt Kochi"
assert len(st7.selected_places) == 2
assert [p.name for p in st7.selected_places] == ["Fort Kochi", "Mattancherry Palace"]
assert len(st7.selected_restaurants) == 1
assert len(st7.selected_cafes) == 1
print("  ✓ Place and hotel state perfectly isolated from food mutations")
print("  ✓ Test 7 Passed!")

# TEST 8: Routing Readiness Check with Food
print("\n[Test 8] Testing is_ready_for_routing with Food...")
st8 = TripState(destination="Kochi")
st8.hotel_selection = hotel
assert st8.is_ready_for_routing() is False  # 0 stops

# With 1 restaurant and 0 places -> ready!
st8.add_restaurant(r_a)
assert st8.is_ready_for_routing() is True

st8.clear_selected_stops()
assert st8.is_ready_for_routing() is False

# With 1 cafe and 0 places -> ready!
st8.add_cafe(c_a)
assert st8.is_ready_for_routing() is True
print("  ✓ is_ready_for_routing correctly recognizes restaurants and cafes as stops")
print("  ✓ Test 8 Passed!")

# TEST 9: apply_trip_state_update with Food
print("\n[Test 9] Testing apply_trip_state_update for Food...")
st9 = TripState(destination="Kochi")
st9.planning_stage = PlanningStage.FOOD_DISCOVERY

change = apply_trip_state_update(st9, {
    "selected_restaurants": [{"data_id": "r_10", "name": "Fusion Bay", "latitude": 9.965, "longitude": 76.242}],
    "selected_cafes": [{"data_id": "c_20", "name": "Mocha Art Cafe", "latitude": 9.958, "longitude": 76.259}],
    "cuisine_preferences": ["Kerala Seafood", "Cafe"],
    "food_price_preference": "mid-range",
})
assert "selected_restaurants" in change.changed_fields
assert "selected_cafes" in change.changed_fields
assert "cuisine_preferences" in change.changed_fields
assert "food_price_preference" in change.changed_fields
assert len(st9.selected_restaurants) == 1
assert len(st9.selected_cafes) == 1
assert st9.planning_stage == PlanningStage.FOOD_SELECTION
assert st9.cuisine_preferences == ["Kerala Seafood", "Cafe"]
assert st9.food_price_preference == "mid-range"

# Test rejection via apply_trip_state_update
change_rej = apply_trip_state_update(st9, {
    "rejected_restaurants": ["Fusion Bay"],
})
assert "rejected_restaurants" in change_rej.changed_fields
assert len(st9.selected_restaurants) == 0
assert "Fusion Bay" in st9.rejected_restaurants
assert st9.planning_stage == PlanningStage.FOOD_SELECTION
print("  ✓ apply_trip_state_update handles restaurants, cafes, rejections, and preferences")
print("  ✓ Test 9 Passed!")

# TEST 10: summary() Output Verification
print("\n[Test 10] Testing summary() Dictionary...")
summ = st9.summary()
assert summ["restaurants_count"] == 0
assert summ["cafes_count"] == 1
assert summ["cafes"] == ["Mocha Art Cafe"]
assert "Fusion Bay" in summ["rejected_restaurants"]
assert "Fusion Bay" in summ["rejected_food"]
assert summ["cuisine_preferences"] == ["Kerala Seafood", "Cafe"]
assert summ["food_price_preference"] == "mid-range"
assert summ["planning_stage"] == "FOOD_SELECTION"
print(f"  ✓ Summary contains full food planning snapshot: {summ['cafes_count']} cafes, {summ['rejected_food']} rejected")
print("  ✓ Test 10 Passed!")

print("\n" + "=" * 60)
print("ALL MILESTONE 2 COMPONENT 1 TESTS PASSED CLEANLY!")
print("=" * 60)
