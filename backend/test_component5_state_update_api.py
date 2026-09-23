"""
Project Musafir — Component 5 Verification: State Update Engine & Agent API Coordination
Verifies:
1. apply_trip_state_update updates hotel_selection and advances stage to PLACE_DISCOVERY
2. apply_trip_state_update handles compound selected_places and sets stage to PLACE_SELECTION
3. apply_trip_state_update handles rejected_places and purges from selected_places
4. POST /agent/chat with FastPath SELECT_ITEMS processes with iterations=0
5. POST /agent/chat with FastPath REJECT_ITEM processes with iterations=0
6. POST /agent/chat with FastPath ROUTE_REQUEST executes optimize_route with iterations=0
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app
from app.agent.state import TripState, PlanningStage, clear_all_states
from app.agent.context import clear_all_sessions, get_or_create_session
from app.agent.state_update import apply_trip_state_update
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.route import OptimizedRoute, RouteStop, RouteSegment

client = TestClient(app)

print("=" * 60)
print("PROJECT MUSAFIR — COMPONENT 5 STATE UPDATE & API COORDINATION")
print("=" * 60)

# TEST 1: apply_trip_state_update stage progression on hotel selection
print("\n[Test 1] Testing hotel selection advances stage to PLACE_DISCOVERY...")
st = TripState(destination="Jaipur")
assert st.planning_stage == PlanningStage.DISCOVERY

hotel_data = {"name": "The Oberoi Rajvilas", "latitude": 26.8812, "longitude": 75.8671, "price_per_night": 25000.0}
change = apply_trip_state_update(st, {"hotel_selection": hotel_data})
assert "hotel_selection" in change.changed_fields
assert "planning_stage" in change.changed_fields
assert st.planning_stage == PlanningStage.PLACE_DISCOVERY
assert st.hotel_selection.name == "The Oberoi Rajvilas"
print("  ✓ Stage cleanly progressed to PLACE_DISCOVERY on hotel selection")
print("  ✓ Test 1 Passed!")

# TEST 2: apply_trip_state_update compound selected_places sets stage to PLACE_SELECTION
print("\n[Test 2] Testing place selection sets stage to PLACE_SELECTION without routing...")
places_data = [
    {"data_id": "p_amber", "name": "Amber Fort", "latitude": 26.9855, "longitude": 75.8513},
    {"data_id": "p_hawa", "name": "Hawa Mahal", "latitude": 26.9239, "longitude": 75.8267},
]
change_places = apply_trip_state_update(st, {"selected_places": places_data})
assert "selected_places" in change_places.changed_fields
assert len(st.selected_places) == 2
assert st.planning_stage == PlanningStage.PLACE_SELECTION
assert st.current_route is None, "Route must not be auto-computed on place selection!"
print(f"  ✓ Added 2 places; planning stage is now {st.planning_stage.value}")
print("  ✓ Test 2 Passed!")

# TEST 3: apply_trip_state_update handles rejected_places
print("\n[Test 3] Testing rejected_places removes stop and updates state...")
change_reject = apply_trip_state_update(st, {"rejected_places": ["Hawa Mahal"]})
assert "rejected_places" in change_reject.changed_fields
assert len(st.selected_places) == 1
assert st.selected_places[0].name == "Amber Fort"
assert "Hawa Mahal" in st.rejected_places
assert st.planning_stage == PlanningStage.PLACE_SELECTION
print("  ✓ Purged Hawa Mahal and added to rejected_places")
print("  ✓ Test 3 Passed!")

# TEST 4: Agent API chat with FastPath SELECT_ITEMS (iterations=0)
print("\n[Test 4] Testing POST /agent/chat with FastPath multi-selection (SELECT_ITEMS)...")
clear_all_sessions()
clear_all_states()

# Create active session with visible places
session_id = "00000000-0000-0000-0000-000000000005"
session = get_or_create_session(session_id)
session.trip_state.destination = "Jaipur"
session.trip_state.hotel_selection = Hotel(name="Fairmont Jaipur", latitude=26.9855, longitude=75.8513)
session.trip_state.planning_stage = PlanningStage.PLACE_SELECTION
session.conversation_context.set_visible_items("place", [
    {"data_id": "p_amber", "name": "Amber Fort", "latitude": 26.9855, "longitude": 75.8513},
    {"data_id": "p_hawa", "name": "Hawa Mahal", "latitude": 26.9239, "longitude": 75.8267},
    {"data_id": "p_jal", "name": "Jal Mahal", "latitude": 26.9534, "longitude": 75.8462},
])

resp_multi = client.post(
    "/agent/chat",
    json={"conversation_id": session_id, "message": "first and third"},
)
assert resp_multi.status_code == 200, f"Error: {resp_multi.text}"
data_multi = resp_multi.json()
assert data_multi["iterations"] == 0, f"Expected 0 iterations for FastPath, got {data_multi['iterations']}"
assert "Amber Fort" in data_multi["response"]
assert "Jal Mahal" in data_multi["response"]
summary_multi = data_multi["state_summary"]
assert summary_multi["places_count"] == 2
assert summary_multi["planning_stage"] == "PLACE_SELECTION"
print(f"  ✓ FastPath response (0 iterations): \"{data_multi['response']}\"")
print(f"  ✓ State summary places: {summary_multi['places']}")
print("  ✓ Test 4 Passed!")

# TEST 5: Agent API chat with FastPath REJECT_ITEM (iterations=0)
print("\n[Test 5] Testing POST /agent/chat with FastPath rejection (REJECT_ITEM)...")
resp_reject = client.post(
    "/agent/chat",
    json={"conversation_id": session_id, "message": "remove Jal Mahal"},
)
assert resp_reject.status_code == 200
data_reject = resp_reject.json()
assert data_reject["iterations"] == 0
assert "removed Jal Mahal" in data_reject["response"]
summary_reject = data_reject["state_summary"]
assert summary_reject["places_count"] == 1
assert summary_reject["places"] == ["Amber Fort"]
assert "Jal Mahal" in summary_reject["rejected_places"]
assert summary_reject["planning_stage"] == "PLACE_SELECTION"
print(f"  ✓ Rejection processed: \"{data_reject['response']}\"")
print(f"  ✓ Remaining places: {summary_reject['places']}, rejected: {summary_reject['rejected_places']}")
print("  ✓ Test 5 Passed!")

# TEST 6: Agent API chat with FastPath ROUTE_REQUEST
print("\n[Test 6] Testing POST /agent/chat with explicit route request...")
mock_optimized_route = OptimizedRoute(
    ordered_stops=[
        RouteStop(id="p1", name="Amber Fort", latitude=26.9855, longitude=75.8513, type="attraction"),
    ],
    segments=[
        RouteSegment(from_stop="Fairmont Jaipur", to_stop="Amber Fort", distance_meters=3500, duration_seconds=420)
    ],
    total_distance_meters=7000,
    total_duration_seconds=840,
    score=98.0,
)

with patch("app.agent.tools.service_optimize_route", return_value=mock_optimized_route):
    resp_route = client.post(
        "/agent/chat",
        json={"conversation_id": session_id, "message": "build the route"},
    )
    assert resp_route.status_code == 200
    data_route = resp_route.json()
    assert data_route["iterations"] == 0
    assert "built the optimal route" in data_route["response"]
    assert "optimize_route" in data_route["tool_calls"]
    summary_route = data_route["state_summary"]
    assert summary_route["has_route"] is True
    assert summary_route["planning_stage"] == "ROUTE_PLANNING"
    print(f"  ✓ Route response: \"{data_route['response']}\"")
    print(f"  ✓ Planning stage transitioned to {summary_route['planning_stage']}")
    print("  ✓ Test 6 Passed!")

print("\n" + "=" * 60)
print("ALL COMPONENT 5 STATE UPDATE & API TESTS PASSED CLEANLY!")
print("=" * 60)
