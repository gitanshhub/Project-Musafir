"""
Project Musafir — Component 4 Verification: Multi-Selection, Rejection, and Route Intent
Verifies:
1. Single place selection still works
2. "first and third" resolves correctly
3. "1st and 3rd" resolves correctly
4. "first, second and last" resolves correctly
5. name-based multi-selection works ("take the caves and the waterfall")
6. "remove the waterfall" resolves correctly
7. "skip the second one" resolves correctly
8. rejected place is removed from selected_places and added to rejected_places
9. rejected place cannot immediately reappear in discovery
10. route intent is recognized deterministically ("build the route")
11. route is not triggered by selection alone (stays in PLACE_SELECTION)
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from unittest.mock import patch
from app.agent.state import TripState, PlanningStage
from app.agent.context import ConversationContext, SessionState, VisibleItemReference
from app.agent.fast_path import resolve_fast_path
from app.agent.planner import decide_next_planning_action, PlanningActionType
from app.agent.tools import search_places_tool
from app.schemas.place import Place
from app.schemas.hotel import Hotel

print("=" * 60)
print("PROJECT MUSAFIR — COMPONENT 4 MULTI-SELECTION & REJECTION")
print("=" * 60)

# Setup Session with Visible Places
session = SessionState(
    conversation_id="test_comp_4",
    trip_state=TripState(
        destination="Jaipur",
        selected_hotel=Hotel(name="Fairmont Jaipur", latitude=26.9855, longitude=75.8513),
        planning_stage=PlanningStage.PLACE_SELECTION,
    ),
    conversation_context=ConversationContext(),
)

session.conversation_context.set_visible_items("place", [
    {"data_id": "p_amber", "name": "Amber Fort", "latitude": 26.9855, "longitude": 75.8513},
    {"data_id": "p_hawa", "name": "Hawa Mahal", "latitude": 26.9239, "longitude": 75.8267},
    {"data_id": "p_water", "name": "Jal Mahal Waterfall", "latitude": 26.9534, "longitude": 75.8462},
    {"data_id": "p_caves", "name": "Elephant Caves", "latitude": 26.9900, "longitude": 75.8600},
])

# TEST 1: Single place selection still works
print("\n[Test 1] Testing single place selection...")
res1 = resolve_fast_path("the second place", session)
assert res1.matched is True
assert res1.intent == "SELECT_ITEM"
assert res1.reference_resolution["name"] == "Hawa Mahal"
assert len(res1.state_updates["selected_places"]) == 1
print("  ✓ Single place selection: Hawa Mahal matched")
print("  ✓ Test 1 Passed!")

# TEST 2: "first and third" resolves correctly
print("\n[Test 2] Testing 'first and third' compound selection...")
res2 = resolve_fast_path("first and third", session)
assert res2.matched is True
assert res2.intent == "SELECT_ITEMS"
items2 = [item["name"] for item in res2.reference_resolution["items"]]
assert items2 == ["Amber Fort", "Jal Mahal Waterfall"], f"Unexpected items: {items2}"
assert len(res2.state_updates["selected_places"]) == 2
print(f"  ✓ Resolved 'first and third': {items2}")
print("  ✓ Test 2 Passed!")

# TEST 3: "1st and 3rd" resolves correctly
print("\n[Test 3] Testing '1st and 3rd' selection...")
res3 = resolve_fast_path("1st and 3rd", session)
assert res3.matched is True
assert res3.intent == "SELECT_ITEMS"
items3 = [item["name"] for item in res3.reference_resolution["items"]]
assert items3 == ["Amber Fort", "Jal Mahal Waterfall"]
print(f"  ✓ Resolved '1st and 3rd': {items3}")
print("  ✓ Test 3 Passed!")

# TEST 4: "first, second and last" resolves correctly
print("\n[Test 4] Testing 'first, second and last' selection...")
res4 = resolve_fast_path("first, second and last", session)
assert res4.matched is True
assert res4.intent == "SELECT_ITEMS"
items4 = [item["name"] for item in res4.reference_resolution["items"]]
assert items4 == ["Amber Fort", "Hawa Mahal", "Elephant Caves"], f"Unexpected: {items4}"
print(f"  ✓ Resolved 'first, second and last': {items4}")
print("  ✓ Test 4 Passed!")

# TEST 5: Name-based multi-selection ("take the caves and the waterfall")
print("\n[Test 5] Testing name-based multi-selection...")
res5 = resolve_fast_path("take the caves and the waterfall", session)
assert res5.matched is True
assert res5.intent == "SELECT_ITEMS"
items5 = [item["name"] for item in res5.reference_resolution["items"]]
assert "Elephant Caves" in items5 and "Jal Mahal Waterfall" in items5, f"Unexpected: {items5}"
print(f"  ✓ Resolved name-based multi-selection: {items5}")
print("  ✓ Test 5 Passed!")

# TEST 6: "remove the waterfall" resolves correctly
print("\n[Test 6] Testing 'remove the waterfall'...")
res6 = resolve_fast_path("remove the waterfall", session)
assert res6.matched is True
assert res6.intent == "REJECT_ITEM"
assert "Waterfall" in res6.reference_resolution["name"]
assert "Jal Mahal Waterfall" in res6.state_updates["rejected_places"]
print(f"  ✓ Resolved rejection: {res6.reference_resolution['name']}")
print("  ✓ Test 6 Passed!")

# TEST 7: "skip the second one" resolves correctly
print("\n[Test 7] Testing 'skip the second one'...")
res7 = resolve_fast_path("skip the second one", session)
assert res7.matched is True
assert res7.intent == "REJECT_ITEM"
assert res7.reference_resolution["name"] == "Hawa Mahal"
print(f"  ✓ Resolved ordinal rejection: {res7.reference_resolution['name']}")
print("  ✓ Test 7 Passed!")

# TEST 8: Rejected place removed from selected_places & added to rejected_places
print("\n[Test 8] Testing rejection mutation on TripState...")
trip = session.trip_state
trip.add_place(Place(data_id="p_water", name="Jal Mahal Waterfall", latitude=26.9534, longitude=75.8462))
trip.add_place(Place(data_id="p_amber", name="Amber Fort", latitude=26.9855, longitude=75.8513))
assert len(trip.selected_places) == 2

trip.reject_place("Jal Mahal Waterfall")
assert len(trip.selected_places) == 1, "Rejected place must be removed from selected_places"
assert trip.selected_places[0].name == "Amber Fort"
assert "Jal Mahal Waterfall" in trip.rejected_places
print("  ✓ Successfully purged from selected_places and added to rejected_places")
print("  ✓ Test 8 Passed!")

# TEST 9: Rejected place cannot immediately reappear in discovery
print("\n[Test 9] Testing rejected place excluded from search_places_tool...")
mock_serpapi_response = {
    "local_results": [
        {"title": "Amber Fort", "data_id": "p_amber", "latitude": 26.9855, "longitude": 75.8513},
        {"title": "Jal Mahal Waterfall", "data_id": "p_water", "latitude": 26.9534, "longitude": 75.8462},
    ]
}
with patch("app.agent.tools.fetch_places_from_serpapi", return_value=mock_serpapi_response):
    p_res = search_places_tool(destination="Jaipur", trip_state=trip, api_key="mock_key")
    returned_names = [p["name"] for p in p_res["places"]]
    assert "Jal Mahal Waterfall" not in returned_names, "Rejected place must not reappear in discovery"
    assert "Amber Fort" in returned_names
    print(f"  ✓ Discovery filtered out rejected place: {returned_names}")
    print("  ✓ Test 9 Passed!")

# TEST 10: Explicit route intent recognized deterministically
print("\n[Test 10] Testing explicit route intent recognition...")
for route_phrase in ["build the route", "optimize route", "plan our route", "calculate route"]:
    res_route = resolve_fast_path(route_phrase, session)
    assert res_route.matched is True, f"Failed on {route_phrase}"
    assert res_route.intent == "ROUTE_REQUEST"
print("  ✓ Explicit route requests resolved deterministically with high confidence")
print("  ✓ Test 10 Passed!")

# TEST 11: Route is NOT triggered by selection alone
print("\n[Test 11] Testing route is NOT triggered by multi-selection alone...")
action = decide_next_planning_action(trip, context=session.conversation_context, user_intent="SELECT_PLACES")
assert action.action_type == PlanningActionType.WAIT_FOR_PLACE_SELECTION
assert action.target_stage == PlanningStage.PLACE_SELECTION
assert action.action_type != PlanningActionType.OPTIMIZE_ROUTE, "Multi-selection must stay in PLACE_SELECTION!"
print(f"  ✓ Planner action after multi-selection: {action.action_type.value} in stage {action.target_stage.value}")
print("  ✓ Test 11 Passed!")

print("\n" + "=" * 60)
print("ALL COMPONENT 4 MULTI-SELECTION & REJECTION TESTS PASSED CLEANLY!")
print("=" * 60)
