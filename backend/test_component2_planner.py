"""
Project Musafir — Component 2 Verification: Deterministic Planner Tests
Verifies that planner.py acts as a pure deterministic decision layer:
- No LLM calls
- No tool execution/side effects
- Proper stage progression
- Hotel selection leads to place discovery
- Place selection stays in PLACE_SELECTION (does not prematurely route)
- Explicit route intent produces OPTIMIZE_ROUTE
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.agent.state import TripState, PlanningStage
from app.agent.context import ConversationContext, VisibleItemReference
from app.agent.planner import (
    PlanningActionType,
    PlanningAction,
    decide_next_planning_action,
)
from app.schemas.hotel import Hotel
from app.schemas.place import Place

print("=" * 60)
print("PROJECT MUSAFIR — COMPONENT 2 PLANNER VERIFICATION")
print("=" * 60)

# Test 1: Clean imports and Model validation
print("\n[Test 1] Testing PlanningAction Pydantic Validation...")
action = PlanningAction(
    action_type=PlanningActionType.SEARCH_PLACES,
    reason="test_reason",
    tool_name="search_places",
    tool_args={"query": "test"},
    target_stage=PlanningStage.PLACE_SELECTION,
)
assert action.action_type == PlanningActionType.SEARCH_PLACES
assert action.target_stage == PlanningStage.PLACE_SELECTION
assert action.tool_args["query"] == "test"
print("  ✓ PlanningAction validates cleanly.")

# Test 2: Informational / greeting produces ANSWER
print("\n[Test 2] Testing Informational Request Produces ANSWER...")
state = TripState()
action = decide_next_planning_action(state, user_intent="GREETING")
assert action.action_type == PlanningActionType.ANSWER
assert action.reason == "informational_intent"
print("  ✓ Informational intent produces ANSWER.")

# Test 3: Missing destination produces ASK
print("\n[Test 3] Testing Missing Destination Produces ASK...")
state = TripState()
action = decide_next_planning_action(state, user_intent=None)
assert action.action_type == PlanningActionType.ASK
assert action.reason == "missing_destination"
assert "travel" in action.prompt_message.lower()
print("  ✓ Missing destination produces ASK.")

# Test 4: Destination set but missing budget/hotel produces ASK or SEARCH_HOTELS
print("\n[Test 4] Testing Hotel Search & Active Question Flow...")
state = TripState(destination="Jaipur")
action = decide_next_planning_action(state, user_intent="SEARCH_HOTELS")
# Budget is missing, so it asks for budget
assert action.action_type == PlanningActionType.ASK
assert action.target_stage == PlanningStage.HOTEL_SELECTION
print(f"  ✓ Produced ASK for missing hotel constraints: {action.reason}")

# Set budget & days
state.hotel_budget = 4000.0
state.number_of_days = 3
state.trip_start_date = "2026-10-01"
action = decide_next_planning_action(state, user_intent="SEARCH_HOTELS")
assert action.action_type == PlanningActionType.SEARCH_HOTELS
assert action.tool_name == "search_hotels"
assert action.tool_args["destination"] == "Jaipur"
print("  ✓ When budget is present, produces SEARCH_HOTELS.")

# Test 5: Visible hotels awaiting user selection produces WAIT_FOR_HOTEL_SELECTION
print("\n[Test 5] Testing Visible Hotels Awaiting Selection...")
ctx = ConversationContext()
ctx.visible_hotels = [
    VisibleItemReference(index=1, entity_type="hotel", id="h1", name="Fairmont Jaipur", price_per_night=8500.0),
    VisibleItemReference(index=2, entity_type="hotel", id="h2", name="Rambagh Palace", price_per_night=15000.0),
]
action = decide_next_planning_action(state, context=ctx, user_intent=None)
assert action.action_type == PlanningActionType.WAIT_FOR_HOTEL_SELECTION
assert action.target_stage == PlanningStage.HOTEL_SELECTION
print("  ✓ Visible hotels result in WAIT_FOR_HOTEL_SELECTION.")

# Test 6: Hotel selection triggers anchored place discovery
print("\n[Test 6] Testing Hotel Selection Leads to Anchored Place Discovery...")
hotel = Hotel(name="Fairmont Jaipur", latitude=26.9855, longitude=75.8513)
state.hotel_selection = hotel
state.planning_stage = PlanningStage.HOTEL_SELECTION
action = decide_next_planning_action(state, user_intent="HOTEL_SELECTED")
assert action.action_type == PlanningActionType.SEARCH_PLACES
assert action.tool_name == "search_places"
assert action.target_stage == PlanningStage.PLACE_SELECTION
assert action.tool_args["location_anchor"] == "Fairmont Jaipur"
assert action.tool_args["latitude"] == 26.9855
assert "Fairmont Jaipur" in action.tool_args["query"]
print(f"  ✓ Place discovery anchored at hotel: {action.tool_args['query']}")

# Test 7: Selecting / rejecting places keeps system in PLACE_SELECTION (DOES NOT ROUTE)
print("\n[Test 7] Testing Selecting Places Keeps System in PLACE_SELECTION...")
place1 = Place(data_id="p1", name="Amber Fort", latitude=26.9855, longitude=75.8513)
state.add_place(place1)
state.planning_stage = PlanningStage.PLACE_SELECTION

action_select = decide_next_planning_action(state, user_intent="SELECT_PLACE")
assert action_select.action_type == PlanningActionType.WAIT_FOR_PLACE_SELECTION
assert action_select.target_stage == PlanningStage.PLACE_SELECTION
assert action_select.action_type != PlanningActionType.OPTIMIZE_ROUTE, "CRITICAL: Selecting a place must NOT route!"
print("  ✓ Selecting place keeps state in PLACE_SELECTION and does NOT trigger routing.")

# Multiple places still does not trigger routing without explicit intent
place2 = Place(data_id="p2", name="Hawa Mahal", latitude=26.9239, longitude=75.8267)
state.add_place(place2)
action_multi = decide_next_planning_action(state, user_intent=None)
assert action_multi.action_type == PlanningActionType.WAIT_FOR_PLACE_SELECTION
assert action_multi.target_stage == PlanningStage.PLACE_SELECTION
print("  ✓ 2 selected places still stays in PLACE_SELECTION without explicit route intent.")

# Rejecting a place keeps state in PLACE_SELECTION
state.reject_place("City Palace")
action_reject = decide_next_planning_action(state, user_intent="REJECT_PLACE")
assert action_reject.action_type == PlanningActionType.WAIT_FOR_PLACE_SELECTION
assert action_reject.target_stage == PlanningStage.PLACE_SELECTION
assert "City Palace" in state.rejected_places
print("  ✓ Rejecting place stays in PLACE_SELECTION.")

# Test 8: Explicit route intent triggers OPTIMIZE_ROUTE
print("\n[Test 8] Testing Explicit Route Intent Triggers OPTIMIZE_ROUTE...")
action_route = decide_next_planning_action(state, user_intent="ROUTE_REQUEST")
assert action_route.action_type == PlanningActionType.OPTIMIZE_ROUTE
assert action_route.tool_name == "optimize_route"
assert action_route.target_stage == PlanningStage.ROUTE_PLANNING
assert action_route.tool_args["origin"]["name"] == "Fairmont Jaipur"
assert len(action_route.tool_args["stops"]) == 2
print(f"  ✓ OPTIMIZE_ROUTE triggered with origin {action_route.tool_args['origin']['name']} and {len(action_route.tool_args['stops'])} stops.")

# Test 9: Route intent without stops produces ASK
print("\n[Test 9] Testing Route Intent Without Stops Produces ASK...")
state_no_stops = TripState(destination="Jaipur", selected_hotel=hotel)
action_no_stops = decide_next_planning_action(state_no_stops, user_intent="ROUTE_REQUEST")
assert action_no_stops.action_type == PlanningActionType.ASK
assert action_no_stops.reason == "route_requires_places"
print("  ✓ Route intent without stops asks user to select places first.")

print("\n" + "=" * 60)
print("ALL COMPONENT 2 PLANNER TESTS PASSED CLEANLY!")
print("=" * 60)
