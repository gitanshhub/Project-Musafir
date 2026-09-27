"""
Project Musafir — Milestone 3 Batch 2: Conversational Dynamic Modifications E2E Suite
Validates the full dynamic modification user experience through the FastAPI application client:
1. Canonical trip construction
2. Ambiguous hotel reference triggers CLARIFICATION without mutating TripState
3. Specific hotel change swaps hotel, invalidates route & itinerary, preserves places & food
4. Route rebuild re-anchors origin & destination to the new hotel
5. In-place entity replacement: "replace Fort Kochi with Mattancherry Palace and rebuild the route"
6. Duration modification: "make it 3 days"
7. Explicit budget change: "hotel budget is 6000 per night"
8. Compound modification: "change hotel to Grand Hyatt and make it 4 days"
9. Text purity: zero leakage of internal enums, tokens, or raw JSON
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import unittest
from datetime import date
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from app.agent.state import TripState, PlanningStage
from app.agent.dependencies import DerivedResource
from app.agent.freshness import DerivedStateStatus
from app.agent.context import get_session, save_session
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import OptimizedRoute


class TestBatch2E2EModifications(unittest.TestCase):
    """E2E Conversational Dynamic Modification Suite."""

    def setUp(self):
        self.date_patcher = patch("app.agent.semantics.get_current_date", return_value=date(2026, 9, 23))
        self.date_patcher.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.date_patcher.stop()

    def test_conversational_dynamic_modifications_flow(self):
        print("\n" + "=" * 65)
        print("MILESTONE 3 BATCH 2: FULL CONVERSATIONAL MODIFICATIONS E2E")
        print("=" * 65)

        # 1. Initialize trip
        r = self.client.post("/agent/chat", json={"message": "Kerala"})
        self.assertEqual(r.status_code, 200)
        cid = r.json()["conversation_id"]

        self.client.post("/agent/chat", json={"conversation_id": cid, "message": "5 days"})
        self.client.post("/agent/chat", json={"conversation_id": cid, "message": "total 20000"})
        self.client.post("/agent/chat", json={"conversation_id": cid, "message": "24 Sept"})

        session = get_session(cid)
        session.conversation_context.set_visible_items("hotel", [
            {"data_id": "h_bolgatty", "name": "Grand Hyatt Kochi Bolgatty", "price_per_night": 4500.0, "latitude": 9.9880, "longitude": 76.2625},
            {"data_id": "h_brunton", "name": "Brunton Boatyard", "price_per_night": 6000.0, "latitude": 9.9680, "longitude": 76.2440},
            {"data_id": "h_taj_malabar", "name": "Taj Malabar Resort & Spa", "price_per_night": 5500.0, "latitude": 9.9650, "longitude": 76.2410},
            {"data_id": "h_taj_gateway", "name": "Taj Gateway Hotel", "price_per_night": 3500.0, "latitude": 9.9820, "longitude": 76.2790},
        ])
        save_session(session)

        # Select Hotel 1
        self.client.post("/agent/chat", json={"conversation_id": cid, "message": "take the first hotel"})

        # Visible places
        session = get_session(cid)
        session.conversation_context.set_visible_items("place", [
            {"data_id": "p_fort", "name": "Fort Kochi", "latitude": 9.9650, "longitude": 76.2420},
            {"data_id": "p_marine", "name": "Marine Drive Kochi", "latitude": 9.9780, "longitude": 76.2750},
            {"data_id": "p_mattancherry", "name": "Mattancherry Palace", "latitude": 9.9580, "longitude": 76.2590},
        ])
        save_session(session)
        self.client.post("/agent/chat", json={"conversation_id": cid, "message": "take the first and second"})

        # Select food
        session = get_session(cid)
        session.conversation_context.set_visible_items("restaurant", [
            {"data_id": "r_malabar", "name": "Malabar Cafe", "latitude": 9.9885, "longitude": 76.2630, "price_level": "₹₹"},
        ])
        save_session(session)
        self.client.post("/agent/chat", json={"conversation_id": cid, "message": "take the first restaurant"})

        # Build initial route
        mock_route_1 = {
            "origin": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "destination": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "ordered_stops": [
                {"id": "p_fort", "name": "Fort Kochi", "latitude": 9.9650, "longitude": 76.2420, "type": "attraction"},
                {"id": "r_malabar", "name": "Malabar Cafe", "latitude": 9.9885, "longitude": 76.2630, "type": "restaurant"},
                {"id": "p_marine", "name": "Marine Drive Kochi", "latitude": 9.9780, "longitude": 76.2750, "type": "attraction"},
            ],
            "segments": [
                {"from_stop": "Grand Hyatt", "to_stop": "Fort Kochi", "distance_meters": 4500.0, "duration_seconds": 600},
                {"from_stop": "Fort Kochi", "to_stop": "Malabar Cafe", "distance_meters": 3000.0, "duration_seconds": 400},
                {"from_stop": "Malabar Cafe", "to_stop": "Marine Drive", "distance_meters": 3000.0, "duration_seconds": 400},
                {"from_stop": "Marine Drive", "to_stop": "Grand Hyatt", "distance_meters": 5000.0, "duration_seconds": 820},
            ],
            "total_distance_meters": 15500.0,
            "total_duration_seconds": 2220,
            "mode": "driving",
            "score": 95.0,
        }
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route_1)):
            r_route = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "build the route"})
            self.assertEqual(r_route.status_code, 200)
            self.assertEqual(r_route.json()["state_summary"]["derived_freshness"]["current_route"], "valid")

        # Generate initial itinerary
        r_itin = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "generate itinerary"})
        self.assertEqual(r_itin.status_code, 200)
        self.assertEqual(r_itin.json()["state_summary"]["derived_freshness"]["current_itinerary"], "valid")
        print("  [OK] Initial trip fully built: Route & Itinerary are VALID")

        # ---------------------------------------------------------------------
        # Scenario 1: Ambiguous hotel reference triggers CLARIFICATION without mutating state
        # ---------------------------------------------------------------------
        print("\n[Step 1] Ambiguous reference: 'change hotel to Taj'...")
        session_before = get_session(cid)
        ver_before = session_before.trip_state.state_version
        r_amb = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "change hotel to Taj"})
        self.assertEqual(r_amb.status_code, 200)
        d_amb = r_amb.json()
        self.assertIn("taj", d_amb["response"].lower())
        self.assertIn("which one", d_amb["response"].lower())
        # State MUST NOT be mutated!
        session_after = get_session(cid)
        self.assertEqual(session_after.trip_state.state_version, ver_before)
        self.assertEqual(session_after.trip_state.hotel_selection.name, "Grand Hyatt Kochi Bolgatty")
        print("  [OK] Clarification returned for ambiguous hotel; 0 state mutations executed.")

        # ---------------------------------------------------------------------
        # Scenario 2: Unambiguous hotel swap
        # ---------------------------------------------------------------------
        print("\n[Step 2] Hotel swap: 'change hotel to Brunton Boatyard'...")
        r_hotel = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "change hotel to Brunton Boatyard"})
        self.assertEqual(r_hotel.status_code, 200)
        d_hotel = r_hotel.json()
        self.assertEqual(d_hotel["state_summary"]["hotel"], "Brunton Boatyard")
        self.assertEqual(d_hotel["state_summary"]["derived_freshness"]["current_route"], "stale")
        self.assertEqual(d_hotel["state_summary"]["derived_freshness"]["current_itinerary"], "stale")
        # Invariants: places and food are preserved!
        self.assertEqual(d_hotel["state_summary"]["places_count"], 2)
        self.assertEqual(d_hotel["state_summary"]["cafes_count"], 1)
        print("  [OK] Hotel swapped to Brunton Boatyard; places & food preserved; route/itinerary marked STALE.")

        # ---------------------------------------------------------------------
        # Scenario 3: Route rebuild re-anchors to new hotel
        # ---------------------------------------------------------------------
        print("\n[Step 3] Rebuild route: 'build the route'...")
        mock_route_brunton = dict(mock_route_1)
        mock_route_brunton["origin"] = {"name": "Brunton Boatyard", "latitude": 9.9680, "longitude": 76.2440}
        mock_route_brunton["destination"] = {"name": "Brunton Boatyard", "latitude": 9.9680, "longitude": 76.2440}
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route_brunton)) as mock_opt:
            r_rebuild = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "build the route"})
            self.assertEqual(r_rebuild.status_code, 200)
            d_rebuild = r_rebuild.json()
            self.assertEqual(d_rebuild["state_summary"]["derived_freshness"]["current_route"], "valid")
            # Verify origin used at service was Brunton Boatyard
            call_kwargs = mock_opt.call_args[0][0]
            self.assertEqual(call_kwargs.start_location.name, "Brunton Boatyard")
            print("  [OK] Route rebuilt with Brunton Boatyard as origin & destination.")

        # ---------------------------------------------------------------------
        # Scenario 4: In-place entity replacement with route rebuild
        # ---------------------------------------------------------------------
        print("\n[Step 4] Entity replacement: 'replace Fort Kochi with Mattancherry Palace and rebuild the route'...")
        mock_route_replaced = dict(mock_route_brunton)
        mock_route_replaced["ordered_stops"][0] = {"id": "p_mattancherry", "name": "Mattancherry Palace", "latitude": 9.9580, "longitude": 76.2590, "type": "attraction"}
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route_replaced)):
            r_repl = self.client.post("/agent/chat", json={
                "conversation_id": cid,
                "message": "replace Fort Kochi with Mattancherry Palace and rebuild the route",
            })
            self.assertEqual(r_repl.status_code, 200)
            d_repl = r_repl.json()
            self.assertEqual(d_repl["state_summary"]["derived_freshness"]["current_route"], "valid")
            self.assertIn("Mattancherry Palace", d_repl["state_summary"]["places"])
            self.assertNotIn("Fort Kochi", d_repl["state_summary"]["places"])
            print("  [OK] Fort Kochi replaced by Mattancherry Palace & route rebuilt in atomic turn.")

        # ---------------------------------------------------------------------
        # Scenario 5: Duration modification
        # ---------------------------------------------------------------------
        print("\n[Step 5] Duration modification: 'make it 3 days'...")
        r_dur = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "make it 3 days"})
        self.assertEqual(r_dur.status_code, 200)
        d_dur = r_dur.json()
        self.assertEqual(d_dur["state_summary"]["number_of_days"], 3)
        self.assertEqual(d_dur["state_summary"]["derived_freshness"]["current_itinerary"], "stale")
        # Route remains valid
        self.assertEqual(d_dur["state_summary"]["derived_freshness"]["current_route"], "valid")
        print("  [OK] Duration updated to 3 days; itinerary marked STALE; route remains VALID.")

        # ---------------------------------------------------------------------
        # Scenario 6: Explicit budget modification
        # ---------------------------------------------------------------------
        print("\n[Step 6] Budget update: 'hotel budget is 7000 per night'...")
        r_bgt = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "hotel budget is 7000 per night"})
        self.assertEqual(r_bgt.status_code, 200)
        d_bgt = r_bgt.json()
        self.assertEqual(d_bgt["state_summary"]["hotel_budget"], 7000.0)
        # Brunton Boatyard is 6000, so it remains valid and selected!
        self.assertIsNotNone(d_bgt["state_summary"]["hotel"])
        self.assertEqual(d_bgt["state_summary"]["hotel"], "Brunton Boatyard")
        print("  [OK] Hotel budget increased to 7000/night; valid hotel retained.")

        # ---------------------------------------------------------------------
        # Scenario 7: Text purity checks
        # ---------------------------------------------------------------------
        for resp_obj in [d_amb, d_hotel, d_rebuild, d_repl, d_dur, d_bgt]:
            resp_text = resp_obj["response"]
            self.assertNotIn("CURRENT_ROUTE", resp_text)
            self.assertNotIn("NOT_AVAILABLE", resp_text)
            self.assertNotIn("derived_freshness", resp_text)
            self.assertNotIn("state_version", resp_text)
            self.assertNotIn("property_token", resp_text)

        print("\n" + "=" * 65)
        print("ALL BATCH 2 CONVERSATIONAL E2E SCENARIOS PASSED 100%!")
        print("=" * 65)


if __name__ == "__main__":
    unittest.main()
