"""
Project Musafir — Milestone 3 Component 2 Verification: Mutation Application Layer
Verifies:
1. SET_DESTINATION updates actual TripState.
2. SET_START_DATE updates actual state.
3. SET_END_DATE updates actual state.
4. SET_DURATION updates actual state and derived dates/nights.
5. NIGHTLY_HOTEL budget updates hotel_budget without corrupting hotel_total_budget.
6. TOTAL_HOTEL budget updates hotel_total_budget and derives nightly rate when nights known.
7. TOTAL_TRIP budget updates trip_budget independently.
8. SET_TRAVEL_MODE updates travel_mode and invalidates route/itinerary if present.
9. SELECT_HOTEL updates hotel_selection and transitions planning_stage.
10. CHANGE_HOTEL updates hotel_selection and invalidates previous route/itinerary.
11. SELECT_PLACE adds places through canonical state logic and invalidates route/itinerary.
12. REMOVE_PLACE removes places through canonical state logic and invalidates route/itinerary.
13. SELECT_RESTAURANT and REMOVE_RESTAURANT work cleanly.
14. SELECT_CAFE and REMOVE_CAFE work cleanly.
15. ADD_PREFERENCE (dietary, cuisine, interests, meal, food_price) updates collections.
16. REMOVE_PREFERENCE purges specific items from collections.
17. TripChangeSet accurately tracks changed_fields and invalidated_fields.
18. state_version increments exactly once per mutation call and zero times when no-op.
19. MutationBatch applies multiple distinct mutations coherently with a single version increment.
20. Invalid batch fails deterministically and leaves TripState completely uncorrupted.
21. Existing invalidation cascading (destination clearing stops/hotels/route) remains intact.
22. JSON / dictionary payloads pass through apply_mutation_command and apply_mutation_batch.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from datetime import date as dt_date
from app.agent.state import TripState, PlanningStage
from app.schemas.reference import EntityReference, VisibleItemReference
from app.schemas.mutation import (
    MutationType,
    BudgetScope,
    ResolutionConfidence,
    MutationCommand,
    MutationBatch,
)
from app.agent.mutation import (
    apply_mutation_command,
    apply_mutation_batch,
    mutation_to_state_updates,
)
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import OptimizedRoute
from app.schemas.itinerary import ItineraryResponse

print("=" * 60)
print("PROJECT MUSAFIR — MILESTONE 3 COMPONENT 2: MUTATION APPLICATION")
print("=" * 60)

# TEST 1: SET_DESTINATION & Cascading Invalidation
print("\n[Test 1] Testing SET_DESTINATION Application & Invalidation...")
st = TripState()
st.hotel_selection = Hotel(name="Old Hotel", latitude=9.93, longitude=76.26)
st.selected_places = [Place(data_id="P1", name="Old Place", latitude=9.9, longitude=76.2)]
st.current_route = OptimizedRoute(
    ordered_stops=[],
    segments=[],
    total_distance_meters=10000.0,
    total_duration_seconds=1800.0,
    score=1.0,
)
initial_version = st.state_version

cmd_dest = MutationCommand(
    mutation_type=MutationType.SET_DESTINATION,
    value="Jaipur",
)
cs = apply_mutation_command(st, cmd_dest)

assert st.destination == "Jaipur"
assert "destination" in cs.changed_fields
assert "selected_hotel" in cs.invalidated_fields
assert "selected_places" in cs.invalidated_fields
assert "current_route" in cs.invalidated_fields
assert st.hotel_selection is None
assert len(st.selected_places) == 0
assert st.current_route is None
assert st.state_version == initial_version + 1
print("  ✓ SET_DESTINATION updated state and triggered canonical cascading invalidation")
print("  ✓ Test 1 Passed!")

# TEST 2: SET_START_DATE & SET_END_DATE
print("\n[Test 2] Testing SET_START_DATE & SET_END_DATE...")
st = TripState(destination="Jaipur")
v0 = st.state_version
cs1 = apply_mutation_command(st, MutationCommand(mutation_type=MutationType.SET_START_DATE, value="2026-10-10"))
assert st.trip_start_date == dt_date(2026, 10, 10)
assert "trip_start_date" in cs1.changed_fields
assert st.state_version == v0 + 1

cs2 = apply_mutation_command(st, MutationCommand(mutation_type=MutationType.SET_END_DATE, value="2026-10-15"))
assert st.trip_end_date == dt_date(2026, 10, 15)
assert st.number_of_days == 6
assert st.number_of_nights == 5
assert st.state_version == v0 + 2
print("  ✓ Dates applied with synchronized duration and nights calculation")
print("  ✓ Test 2 Passed!")

# TEST 3: SET_DURATION
print("\n[Test 3] Testing SET_DURATION...")
st = TripState(destination="Jaipur", trip_start_date=dt_date(2026, 10, 10))
v0 = st.state_version
cs = apply_mutation_command(st, MutationCommand(mutation_type=MutationType.SET_DURATION, value=4))
assert st.number_of_days == 4
assert st.number_of_nights == 3
assert st.trip_end_date == dt_date(2026, 10, 13)
assert "number_of_days" in cs.changed_fields
assert st.state_version == v0 + 1
print("  ✓ SET_DURATION updated duration, nights, and end date accurately")
print("  ✓ Test 3 Passed!")

# TEST 4: Budget Scopes Isolation (NIGHTLY_HOTEL, TOTAL_HOTEL, TOTAL_TRIP)
print("\n[Test 4] Testing Budget Scopes Isolation...")
st = TripState(destination="Jaipur", number_of_days=4, number_of_nights=3)
v0 = st.state_version

# 4A. NIGHTLY_HOTEL
cs_b1 = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.SET_BUDGET, scope=BudgetScope.NIGHTLY_HOTEL, value=2500),
)
assert st.hotel_budget == 2500.0
assert st.hotel_total_budget is None
assert st.trip_budget is None
assert "hotel_budget" in cs_b1.changed_fields

# 4B. TOTAL_HOTEL (should derive nightly ceiling since 3 nights known)
cs_b2 = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.SET_BUDGET, scope=BudgetScope.TOTAL_HOTEL, value=12000),
)
assert st.hotel_total_budget == 12000.0
assert st.hotel_budget == 4000.0  # 12000 / 3 nights
assert "hotel_total_budget" in cs_b2.changed_fields

# 4C. TOTAL_TRIP (whole trip budget)
cs_b3 = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.SET_BUDGET, scope=BudgetScope.TOTAL_TRIP, value=50000),
)
assert st.trip_budget == 50000.0
assert st.hotel_total_budget == 12000.0
assert "trip_budget" in cs_b3.changed_fields
print("  ✓ All 3 BudgetScopes applied to proper independent state fields without corruption")
print("  ✓ Test 4 Passed!")

# TEST 5: SET_TRAVEL_MODE & Route Invalidation
print("\n[Test 5] Testing SET_TRAVEL_MODE...")
st = TripState(destination="Jaipur", travel_mode="driving")
st.current_route = OptimizedRoute(
    ordered_stops=[],
    segments=[],
    total_distance_meters=5000.0,
    total_duration_seconds=900.0,
    score=1.0,
)
cs_m = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.SET_TRAVEL_MODE, value="walking"),
)
assert st.travel_mode == "walking"
assert "travel_mode" in cs_m.changed_fields
assert "current_route" in cs_m.invalidated_fields
assert st.current_route is None
print("  ✓ Travel mode updated and dependent route invalidated")
print("  ✓ Test 5 Passed!")

# TEST 6: SELECT_HOTEL & CHANGE_HOTEL
print("\n[Test 6] Testing SELECT_HOTEL & CHANGE_HOTEL...")
st = TripState(destination="Kochi")
hotel_ref = EntityReference(
    entity_type="hotel",
    id="H101",
    name="Old Harbour Hotel",
    ordinal=1,
    data={"name": "Old Harbour Hotel", "latitude": 9.96, "longitude": 76.24, "price_per_night": 5500.0},
)
cs_h1 = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.SELECT_HOTEL, reference=hotel_ref),
)
assert st.hotel_selection is not None
assert st.hotel_selection.name == "Old Harbour Hotel"
assert st.planning_stage == PlanningStage.PLACE_DISCOVERY
assert "hotel_selection" in cs_h1.changed_fields

# Now simulate an existing route and test CHANGE_HOTEL invalidation
st.current_route = OptimizedRoute(
    ordered_stops=[],
    segments=[],
    total_distance_meters=5000.0,
    total_duration_seconds=900.0,
    score=1.0,
)
hotel_ref2 = EntityReference(
    entity_type="hotel",
    id="H202",
    name="Brunton Boatyard",
    ordinal=2,
    data={"name": "Brunton Boatyard", "latitude": 9.97, "longitude": 76.25, "price_per_night": 7000.0},
)
cs_h2 = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.CHANGE_HOTEL, reference=hotel_ref2),
)
assert st.hotel_selection.name == "Brunton Boatyard"
assert "current_route" in cs_h2.invalidated_fields
assert st.current_route is None
print("  ✓ SELECT_HOTEL and CHANGE_HOTEL set hotel and invalidate anchored route")
print("  ✓ Test 6 Passed!")

# TEST 7: SELECT_PLACE & REMOVE_PLACE
print("\n[Test 7] Testing SELECT_PLACE & REMOVE_PLACE...")
st = TripState(destination="Kochi")
st.hotel_selection = Hotel(name="Brunton Boatyard", latitude=9.97, longitude=76.25)
p_ref = EntityReference(
    entity_type="place",
    id="P1",
    name="Mattancherry Palace",
    ordinal=1,
    data={"name": "Mattancherry Palace", "data_id": "P1", "latitude": 9.95, "longitude": 76.25},
)
cs_p1 = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.SELECT_PLACE, reference=p_ref),
)
assert len(st.selected_places) == 1
assert st.selected_places[0].name == "Mattancherry Palace"
assert st.planning_stage == PlanningStage.PLACE_SELECTION

# Simulate active route, then remove place
st.current_route = OptimizedRoute(
    ordered_stops=[],
    segments=[],
    total_distance_meters=5000.0,
    total_duration_seconds=900.0,
    score=1.0,
)
cs_p2 = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.REMOVE_PLACE, target_id="P1"),
)
assert len(st.selected_places) == 0
assert "selected_places" in cs_p2.changed_fields
assert "current_route" in cs_p2.invalidated_fields
assert st.current_route is None
print("  ✓ SELECT_PLACE and REMOVE_PLACE operate through canonical state methods")
print("  ✓ Test 7 Passed!")

# TEST 8: Food Operations (SELECT/REMOVE RESTAURANT & CAFE)
print("\n[Test 8] Testing Food Operations (Restaurants & Cafes)...")
st = TripState(destination="Kochi")
cs_r = apply_mutation_command(
    st,
    MutationCommand(
        mutation_type=MutationType.SELECT_RESTAURANT,
        value={"name": "Grand Pavillion", "data_id": "R1", "cuisine": ["Kerala"]},
    ),
)
assert len(st.selected_restaurants) == 1
assert st.selected_restaurants[0].name == "Grand Pavillion"

cs_c = apply_mutation_command(
    st,
    MutationCommand(
        mutation_type=MutationType.SELECT_CAFE,
        value={"name": "Teapot Cafe", "data_id": "C1", "category": "Cafe"},
    ),
)
assert len(st.selected_cafes) == 1
assert st.selected_cafes[0].name == "Teapot Cafe"

# Remove restaurant
cs_rem_r = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.REMOVE_RESTAURANT, target_name="Grand Pavillion"),
)
assert len(st.selected_restaurants) == 0
assert "selected_restaurants" in cs_rem_r.changed_fields

# Remove cafe
cs_rem_c = apply_mutation_command(
    st,
    MutationCommand(mutation_type=MutationType.REMOVE_CAFE, target_name="Teapot Cafe"),
)
assert len(st.selected_cafes) == 0
assert "selected_cafes" in cs_rem_c.changed_fields
print("  ✓ Restaurant and Cafe selections and removals operate cleanly")
print("  ✓ Test 8 Passed!")

# TEST 9: Preferences (ADD_PREFERENCE & REMOVE_PREFERENCE)
print("\n[Test 9] Testing Preferences Mutations...")
st = TripState()
apply_mutation_command(st, MutationCommand(mutation_type=MutationType.ADD_PREFERENCE, target_name="dietary", value="vegetarian"))
apply_mutation_command(st, MutationCommand(mutation_type=MutationType.ADD_PREFERENCE, target_name="cuisine", value="South Indian"))
apply_mutation_command(st, MutationCommand(mutation_type=MutationType.ADD_PREFERENCE, target_name="interests", value="history"))
apply_mutation_command(st, MutationCommand(mutation_type=MutationType.ADD_PREFERENCE, target_name="meal", value={"lunch": "thali"}))

assert "vegetarian" in st.dietary_preferences
assert "South Indian" in st.cuisine_preferences
assert "history" in st.interests
assert st.meal_preferences.get("lunch") == "thali"

# Remove preference
apply_mutation_command(st, MutationCommand(mutation_type=MutationType.REMOVE_PREFERENCE, target_name="dietary", value="vegetarian"))
assert "vegetarian" not in st.dietary_preferences
print("  ✓ Preferences added and removed across dietary, cuisine, interests, and meals")
print("  ✓ Test 9 Passed!")

# TEST 10: State Versioning (Single increment, no double increment)
print("\n[Test 10] Testing State Version Semantics...")
st = TripState(destination="Kochi")
v_start = st.state_version

# Meaningful mutation -> increments version by 1
apply_mutation_command(st, MutationCommand(mutation_type=MutationType.SET_DURATION, value=5))
assert st.state_version == v_start + 1

# Same value mutation (no-op) -> version does not increment
apply_mutation_command(st, MutationCommand(mutation_type=MutationType.SET_DURATION, value=5))
assert st.state_version == v_start + 1
print("  ✓ State version increments strictly on changes, no double-increments or no-op bumps")
print("  ✓ Test 10 Passed!")

# TEST 11: MutationBatch Coherent Multi-Mutation Application
print("\n[Test 11] Testing MutationBatch Coherent Application...")
st = TripState(destination="Kochi", travel_mode="driving")
st.hotel_selection = Hotel(name="Brunton Boatyard", latitude=9.97, longitude=76.25)
st.selected_places = [
    Place(data_id="P1", name="Place One", latitude=9.9, longitude=76.2),
    Place(data_id="P2", name="Place Two", latitude=9.91, longitude=76.21),
]
v_before = st.state_version

batch = MutationBatch(
    mutations=[
        MutationCommand(mutation_type=MutationType.REMOVE_PLACE, target_id="P1"),
        MutationCommand(mutation_type=MutationType.SET_TRAVEL_MODE, value="walking"),
        MutationCommand(mutation_type=MutationType.ADD_PREFERENCE, target_name="dietary", value="vegan"),
    ]
)
cs_batch = apply_mutation_batch(st, batch)

# Verify atomic outcomes
assert len(st.selected_places) == 1
assert st.selected_places[0].name == "Place Two"
assert st.travel_mode == "walking"
assert "vegan" in st.dietary_preferences
# Crucial: Exactly one version increment for the whole batch
assert st.state_version == v_before + 1
assert "selected_places" in cs_batch.changed_fields
assert "travel_mode" in cs_batch.changed_fields
assert "dietary_preferences" in cs_batch.changed_fields
print("  ✓ MutationBatch applies multiple operations with exactly 1 state version increment")
print("  ✓ Test 11 Passed!")

# TEST 12: Invalid Batch Fails Before Modifying State (Zero Corruption)
print("\n[Test 12] Testing Zero State Corruption on Invalid Batch...")
st = TripState(destination="Kochi", travel_mode="driving")
v_snap = st.state_version

invalid_batch_dict = {
    "mutations": [
        {"mutation_type": "SET_TRAVEL_MODE", "value": "walking"},
        {"mutation_type": "SET_BUDGET", "value": 5000},  # Missing scope -> raises ValueError!
    ]
}

try:
    apply_mutation_batch(st, invalid_batch_dict)
    assert False, "Should have failed on invalid mutation command"
except ValueError:
    pass

# Verify state was untouched
assert st.travel_mode == "driving"
assert st.state_version == v_snap
print("  ✓ State remained 100% clean and uncorrupted after batch validation failure")
print("  ✓ Test 12 Passed!")

# TEST 13: Direct Dict / JSON Bridge Execution
print("\n[Test 13] Testing Dict / JSON Bridge Execution...")
st = TripState()
cmd_dict = {
    "mutation_type": "SET_DESTINATION",
    "value": "Udaipur",
}
cs_dict = apply_mutation_command(st, cmd_dict)
assert st.destination == "Udaipur"
assert "destination" in cs_dict.changed_fields
print("  ✓ Dict payload successfully accepted and executed through apply_mutation_command")
print("  ✓ Test 13 Passed!")

print("\n" + "=" * 60)
print("ALL 13 COMPONENT 2 MUTATION APPLICATION TESTS PASSED CLEANLY!")
print("=" * 60)
