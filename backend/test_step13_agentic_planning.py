"""
Project Musafir — Step 13 Milestone 1 E2E Integration Suite: Agentic Travel Planning
Verifies the complete Milestone 1 Agentic Flow:
1. Destination understanding ("Kerala")
2. Duration understanding ("5 days")
3. Total budget understanding ("₹5000 total" / "total 5000" -> ₹1250/night)
4. Travel date resolution ("24 Sept")
5. Hotel discovery & selection ("Grand Hyatt Kochi" -> stage: PLACE_DISCOVERY)
6. Anchored place discovery near hotel (coordinates ll & location_anchor)
7. Compound place selection ("first and third" -> stage: PLACE_SELECTION, no premature routing)
8. Rejection and exclusion ("remove Fort Kochi" -> purged & persisted in rejected_places)
9. Re-selection / place updates
10. Explicit route request ("build the route" -> executes optimize_route, stage: ROUTE_PLANNING)
11. Deterministic planner alignment at every step (no second LLM)
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import uuid
import unittest
from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from app.agent.state import TripState, PlanningStage, clear_all_states
from app.agent.context import (
    clear_all_sessions,
    get_or_create_session,
    get_session,
    save_session,
    VisibleItemReference,
)
from app.agent.planner import decide_next_planning_action, PlanningActionType
from app.agent.tools import search_places_tool, optimize_route_tool, execute_tool
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.route import OptimizedRoute, RouteStop, RouteSegment


class TestStep13AgenticPlanning(unittest.TestCase):
    """Milestone 1 Agentic Travel Planning E2E Integration Suite."""

    def setUp(self):
        clear_all_sessions()
        clear_all_states()
        self.date_patcher = patch("app.agent.semantics.get_current_date", return_value=date(2026, 9, 23))
        self.date_patcher.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.date_patcher.stop()
        clear_all_sessions()
        clear_all_states()

    def test_milestone_1_full_agentic_planning_lifecycle(self):
        """
        Executes the exact Milestone 1 End-to-End flow:
        Kerala → 5 days → ₹5000 total → 24 Sept → select hotel
        → discover places near hotel → select first + third → rejection test → build route.
        """
        print("\n" + "=" * 65)
        print("STEP 13: MILESTONE 1 E2E AGENTIC TRAVEL PLANNING FLOW")
        print("=" * 65)

        cid = str(uuid.uuid4())
        session = get_or_create_session(cid)

        # ---------------------------------------------------------------------
        # Turn 1: Destination ("Kerala")
        # ---------------------------------------------------------------------
        print("\n[Turn 1] User specifies destination: 'Kerala'...")
        res1 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "Trip to Kerala",
        })
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1["state_summary"]["destination"], "Kerala")
        self.assertEqual(data1["state_summary"]["planning_stage"], "DISCOVERY")
        print(f"  ✓ Destination captured: {data1['state_summary']['destination']}")

        # ---------------------------------------------------------------------
        # Turn 2: Duration ("5 days")
        # ---------------------------------------------------------------------
        print("\n[Turn 2] User specifies duration: '5 days'...")
        res2 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "5 days",
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["state_summary"]["number_of_days"], 5)
        self.assertEqual(data2["state_summary"]["number_of_nights"], 4)
        print(f"  ✓ Duration captured: {data2['state_summary']['number_of_days']} days, {data2['state_summary']['number_of_nights']} nights")

        # ---------------------------------------------------------------------
        # Turn 3: Budget ("total 5000")
        # ---------------------------------------------------------------------
        print("\n[Turn 3] User specifies budget: 'total 5000'...")
        res3 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "total 5000",
        })
        self.assertEqual(res3.status_code, 200)
        data3 = res3.json()
        self.assertEqual(data3["iterations"], 0)
        self.assertEqual(data3["state_summary"]["hotel_total_budget"], 5000.0)
        self.assertEqual(data3["state_summary"]["hotel_budget"], 1250.0)
        print(f"  ✓ Budget derived: total ₹{data3['state_summary']['hotel_total_budget']} -> ₹{data3['state_summary']['hotel_budget']}/night")

        # ---------------------------------------------------------------------
        # Turn 4: Dates ("24 Sept" -> 2026-09-24 to 2026-09-28)
        # ---------------------------------------------------------------------
        print("\n[Turn 4] User specifies dates: '24 Sept'...")
        res4 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "24 Sept",
        })
        self.assertEqual(res4.status_code, 200)
        data4 = res4.json()
        self.assertEqual(data4["iterations"], 0)
        self.assertEqual(data4["state_summary"]["dates"], "2026-09-24 to 2026-09-28")
        print(f"  ✓ Dates resolved: {data4['state_summary']['dates']}")

        # Verify deterministic planner state before hotel search
        current_session = get_session(cid)
        action_before_hotels = decide_next_planning_action(current_session.trip_state)
        self.assertEqual(action_before_hotels.action_type, PlanningActionType.SEARCH_HOTELS)
        print("  ✓ Deterministic planner prescribes: SEARCH_HOTELS")

        # ---------------------------------------------------------------------
        # Turn 5: Hotel Search Results & User Hotel Selection
        # ---------------------------------------------------------------------
        print("\n[Turn 5] User selects hotel from visible options...")
        # Simulate hotel search results rendered on screen
        current_session.conversation_context.set_visible_items("hotel", [
            {
                "data_id": "h_hyatt",
                "name": "Grand Hyatt Kochi Bolgatty",
                "latitude": 9.9880,
                "longitude": 76.2625,
                "price_per_night": 1200.0,
                "rating": 4.8,
            },
            {
                "data_id": "h_taj",
                "name": "Taj Malabar Resort & Spa",
                "latitude": 9.9650,
                "longitude": 76.2550,
                "price_per_night": 1250.0,
                "rating": 4.7,
            },
        ])
        save_session(current_session)

        # User chooses the first hotel
        res5 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "the first hotel",
        })
        self.assertEqual(res5.status_code, 200)
        data5 = res5.json()
        self.assertEqual(data5["iterations"], 0)
        self.assertEqual(data5["state_summary"]["hotel"], "Grand Hyatt Kochi Bolgatty")
        self.assertEqual(data5["state_summary"]["planning_stage"], "PLACE_DISCOVERY")
        print(f"  ✓ Hotel selected: {data5['state_summary']['hotel']}")
        print(f"  ✓ Planning stage cleanly progressed to: {data5['state_summary']['planning_stage']}")

        # Verify deterministic planner prescribes place discovery anchored to hotel
        current_session = get_session(cid)
        action_after_hotel = decide_next_planning_action(current_session.trip_state)
        self.assertEqual(action_after_hotel.action_type, PlanningActionType.SEARCH_PLACES)
        self.assertIn("Grand Hyatt Kochi Bolgatty", action_after_hotel.tool_args["location_anchor"])
        print(f"  ✓ Planner prescribes anchored discovery: {action_after_hotel.tool_args}")

        # ---------------------------------------------------------------------
        # Turn 6: Anchored Place Discovery Near Hotel
        # ---------------------------------------------------------------------
        print("\n[Turn 6] Tool executes anchored place discovery near selected hotel...")
        mock_places = [
            Place(data_id="p_fort", name="Fort Kochi", latitude=9.9656, longitude=76.2421, category="attraction"),
            Place(data_id="p_palace", name="Mattancherry Palace", latitude=9.9583, longitude=76.2592, category="attraction"),
            Place(data_id="p_marine", name="Marine Drive Kochi", latitude=9.9790, longitude=76.2755, category="attraction"),
            Place(data_id="p_cherai", name="Cherai Beach", latitude=10.1416, longitude=76.1786, category="attraction"),
        ]

        from app.services.place_service import build_place_search_params
        with patch("app.agent.tools.build_place_search_params", wraps=build_place_search_params) as mock_params, \
             patch("app.agent.tools.fetch_places_from_serpapi", return_value={"local_results": []}), \
             patch("app.agent.tools.normalize_places_response", return_value=mock_places):
            tool_res = execute_tool(
                name="search_places",
                arguments={"query": "attractions near hotel"},
                trip_state=current_session.trip_state,
            )
            # Verify coordinates center ll was passed to place search
            mock_params.assert_called_once()
            call_kwargs = mock_params.call_args[1]
            self.assertEqual(call_kwargs["latitude"], 9.9880)
            self.assertEqual(call_kwargs["longitude"], 76.2625)
            self.assertEqual(call_kwargs["location_anchor"], "Grand Hyatt Kochi Bolgatty")
            print("  ✓ search_places_tool correctly injected hotel coordinates ll (9.988, 76.2625)")

        # Render discovered places to conversation context
        current_session.conversation_context.set_visible_items("place", [p.model_dump() for p in mock_places])
        save_session(current_session)

        # ---------------------------------------------------------------------
        # Turn 7: Compound Place Selection ("first and third")
        # ---------------------------------------------------------------------
        print("\n[Turn 7] User multi-selects 'first and third'...")
        res7 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "first and third",
        })
        self.assertEqual(res7.status_code, 200)
        data7 = res7.json()
        self.assertEqual(data7["iterations"], 0)
        self.assertIn("Fort Kochi", data7["response"])
        self.assertIn("Marine Drive Kochi", data7["response"])
        self.assertEqual(data7["state_summary"]["places_count"], 2)
        self.assertEqual(data7["state_summary"]["places"], ["Fort Kochi", "Marine Drive Kochi"])
        # CRITICAL ARCHITECTURAL ASSERTION: Stage MUST remain in PLACE_SELECTION!
        self.assertEqual(data7["state_summary"]["planning_stage"], "PLACE_SELECTION")
        self.assertFalse(data7["state_summary"]["has_route"])
        print(f"  ✓ Multi-selection processed (0 iterations): {data7['state_summary']['places']}")
        print(f"  ✓ Confirmed stage remains: {data7['state_summary']['planning_stage']} (no premature routing!)")

        # Verify deterministic planner keeps action as WAIT_FOR_PLACE_SELECTION
        current_session = get_session(cid)
        action_after_places = decide_next_planning_action(current_session.trip_state)
        self.assertEqual(action_after_places.action_type, PlanningActionType.WAIT_FOR_PLACE_SELECTION)
        print("  ✓ Planner prescribes: WAIT_FOR_PLACE_SELECTION")

        # ---------------------------------------------------------------------
        # Turn 8: Rejection / Exclusion ("remove Fort Kochi")
        # ---------------------------------------------------------------------
        print("\n[Turn 8] User rejects an item: 'remove Fort Kochi'...")
        res8 = self.client.post("/agent/chat", json={
            "conversation_id": cid,
            "message": "remove Fort Kochi",
        })
        self.assertEqual(res8.status_code, 200)
        data8 = res8.json()
        self.assertEqual(data8["iterations"], 0)
        self.assertIn("removed Fort Kochi", data8["response"])
        self.assertEqual(data8["state_summary"]["places_count"], 1)
        self.assertEqual(data8["state_summary"]["places"], ["Marine Drive Kochi"])
        self.assertIn("Fort Kochi", data8["state_summary"]["rejected_places"])
        self.assertEqual(data8["state_summary"]["planning_stage"], "PLACE_SELECTION")
        print(f"  ✓ Purged from selected: {data8['state_summary']['places']}")
        print(f"  ✓ Tracked in rejected_places: {data8['state_summary']['rejected_places']}")

        # ---------------------------------------------------------------------
        # Turn 9: Rejection Exclusion Verification in Subsequent Search
        # ---------------------------------------------------------------------
        print("\n[Turn 9] Verifying rejected place is excluded from subsequent discovery...")
        with patch("app.agent.tools.fetch_places_from_serpapi", return_value={"local_results": []}), \
             patch("app.agent.tools.normalize_places_response", return_value=mock_places):
            places_res = search_places_tool(query="more places", trip_state=current_session.trip_state)
            place_names = [p["name"] for p in places_res["places"]]
            self.assertNotIn("Fort Kochi", place_names)
            self.assertIn("Mattancherry Palace", place_names)
            self.assertIn("Cherai Beach", place_names)
            print(f"  ✓ Fort Kochi successfully filtered out of discovery: {place_names}")

        # Add Mattancherry Palace back to have 2 places for route
        current_session.trip_state.add_place(mock_places[1])
        save_session(current_session)

        # ---------------------------------------------------------------------
        # Turn 10: Explicit Route Intent ("build the route")
        # ---------------------------------------------------------------------
        print("\n[Turn 10] User gives explicit route intent: 'build the route'...")
        mock_route = OptimizedRoute(
            ordered_stops=[
                RouteStop(id="p_palace", name="Mattancherry Palace", latitude=9.9583, longitude=76.2592, type="attraction"),
                RouteStop(id="p_marine", name="Marine Drive Kochi", latitude=9.9790, longitude=76.2755, type="attraction"),
            ],
            segments=[
                RouteSegment(from_stop="Grand Hyatt Kochi Bolgatty", to_stop="Mattancherry Palace", distance_meters=6200, duration_seconds=900),
                RouteSegment(from_stop="Mattancherry Palace", to_stop="Marine Drive Kochi", distance_meters=5100, duration_seconds=780),
                RouteSegment(from_stop="Marine Drive Kochi", to_stop="Grand Hyatt Kochi Bolgatty", distance_meters=2300, duration_seconds=360),
            ],
            total_distance_meters=13600,
            total_duration_seconds=2040,
            score=95.5,
        )

        with patch("app.agent.tools.service_optimize_route", return_value=mock_route) as mock_opt:
            res10 = self.client.post("/agent/chat", json={
                "conversation_id": cid,
                "message": "build the route",
            })
            self.assertEqual(res10.status_code, 200)
            data10 = res10.json()
            self.assertEqual(data10["iterations"], 0)
            self.assertIn("optimize_route", data10["tool_calls"])
            self.assertIn("13.6 km", data10["response"])
            self.assertTrue(data10["state_summary"]["has_route"])
            self.assertEqual(data10["state_summary"]["planning_stage"], "ROUTE_PLANNING")
            print(f"  ✓ Route response: \"{data10['response']}\"")
            print(f"  ✓ Planning stage transitioned to: {data10['state_summary']['planning_stage']}")

        # Verify planner transitions to ROUTE_PLANNING stage once route exists
        final_session = get_session(cid)
        final_action = decide_next_planning_action(final_session.trip_state)
        self.assertEqual(final_action.target_stage, PlanningStage.ROUTE_PLANNING)
        print(f"  ✓ Planner maintains target stage: {final_action.target_stage}")

        print("\n" + "=" * 65)
        print("MILESTONE 1 E2E AGENTIC TRAVEL PLANNING SUITE PASSED 100%!")
        print("=" * 65)


if __name__ == "__main__":
    unittest.main()
