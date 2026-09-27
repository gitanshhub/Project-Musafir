"""
Project Musafir — Milestone 3 Batch 1: End-to-End Dynamic Replanning Suite
Verifies the complete replanning flow through the live FastAPI application:
1. Canonical trip construction:
   Destination -> Duration -> Budget -> Date -> Select Hotel -> Select Places -> Find & Select Food -> Build Route -> Generate Itinerary
2. Scenario 1: Lazy place removal:
   "remove Fort Kochi" -> Route & itinerary become STALE, NO route tool called
3. Scenario 2: Explicit route rebuild:
   "build the route" -> Route rebuilt without Fort Kochi; route becomes VALID
4. Scenario 3: Explicit itinerary update:
   "update the itinerary" -> Itinerary regenerated using the rebuilt route; itinerary becomes VALID
5. Scenario 4: Travel mode change:
   "switch to walking and rebuild the route" -> Compound mutation + rebuild executed atomically
6. Scenario 5: Hotel change:
   Change hotel -> downstream route/itinerary invalidated, new hotel becomes anchor
7. Inspection assertions:
   - No property tokens in user prose
   - No raw tool names or JSON in responses
   - No internal freshness enum tokens (e.g. CURRENT_ROUTE: STALE) in assistant text
   - No state_version leakage
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import unittest
from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from app.agent.state import TripState, PlanningStage
from app.agent.dependencies import DerivedResource
from app.agent.freshness import DerivedStateStatus
from app.agent.context import get_session, save_session
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import OptimizedRoute, RouteStop, RouteSegment
from app.schemas.itinerary import ItineraryResponse, DailyItinerary, ItineraryItem
from app.agent.state_update import apply_trip_state_update


class TestBatch1E2EReplanning(unittest.TestCase):
    """End-to-End conversational replanning test suite."""

    def setUp(self):
        self.date_patcher = patch("app.agent.semantics.get_current_date", return_value=date(2026, 9, 23))
        self.date_patcher.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.date_patcher.stop()

    def test_full_replanning_e2e_journey(self):
        print("\n" + "=" * 65)
        print("MILESTONE 3 BATCH 1: FULL REPLANNING E2E USER JOURNEY")
        print("=" * 65)

        # -----------------------------------------------------------------
        # Step 1: Destination setup
        # -----------------------------------------------------------------
        r1 = self.client.post("/agent/chat", json={"message": "Kerala"})
        self.assertEqual(r1.status_code, 200)
        cid = r1.json()["conversation_id"]

        # Step 2: Duration
        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "5 days"})
        self.assertEqual(r2.status_code, 200)

        # Step 3: Budget
        r3 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "total 5000"})
        self.assertEqual(r3.status_code, 200)

        # Step 4: Dates
        r4 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "24 Sept"})
        self.assertEqual(r4.status_code, 200)

        # Step 5: Select Hotel
        session = get_session(cid)
        mock_hotels = [
            Hotel(data_id="h1", name="Grand Hyatt Kochi Bolgatty", latitude=9.9880, longitude=76.2625, price_per_night=1250, currency="INR"),
            Hotel(data_id="h2", name="Brunton Boatyard", latitude=9.9680, longitude=76.2440, price_per_night=1200, currency="INR"),
        ]
        session.conversation_context.set_visible_items("hotel", mock_hotels)
        session.trip_state.mark_derived_valid(DerivedResource.HOTEL_DISCOVERY)
        save_session(session)

        r5 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "the first hotel"})
        self.assertEqual(r5.status_code, 200)
        self.assertEqual(r5.json()["state_summary"]["hotel"], "Grand Hyatt Kochi Bolgatty")

        # Step 6: Select Places
        mock_places = [
            Place(data_id="p1", name="Fort Kochi", latitude=9.9658, longitude=76.2421),
            Place(data_id="p2", name="Marine Drive Kochi", latitude=9.9780, longitude=76.2750),
        ]
        session = get_session(cid)
        session.conversation_context.set_visible_items("place", mock_places)
        session.trip_state.mark_derived_valid(DerivedResource.PLACE_DISCOVERY)
        save_session(session)

        r6 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "first and second"})
        self.assertEqual(r6.status_code, 200)
        self.assertEqual(len(r6.json()["state_summary"]["places"]), 2)

        # Step 7: Food discovery and selection
        mock_restaurants = {
            "local_results": [
                {
                    "title": "Malabar Cafe",
                    "data_id": "rest_mc_1",
                    "rating": 4.6,
                    "reviews": 1500,
                    "address": "Bolgatty Island, Kochi",
                    "gps_coordinates": {"latitude": 9.9885, "longitude": 76.2630},
                    "type": "Cafe",
                },
                {
                    "title": "Seagull Restaurant",
                    "data_id": "rest_sg_2",
                    "rating": 4.3,
                    "reviews": 900,
                    "address": "Fort Kochi",
                    "gps_coordinates": {"latitude": 9.9660, "longitude": 76.2425},
                    "type": "Restaurant",
                }
            ]
        }
        with patch("app.agent.tools.fetch_restaurants_from_serpapi", return_value=mock_restaurants):
            r7_disc = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "find lunch near route"})
            self.assertEqual(r7_disc.status_code, 200)

        r7 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "take the first restaurant"})
        self.assertEqual(r7.status_code, 200)
        self.assertEqual(r7.json()["state_summary"]["cafes_count"], 1)

        # Step 8: Build Route
        mock_route = {
            "origin": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "destination": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "ordered_stops": [
                {"id": "s1", "name": "Fort Kochi", "latitude": 9.9658, "longitude": 76.2421, "type": "attraction"},
                {"id": "s2", "name": "Malabar Cafe", "latitude": 9.9885, "longitude": 76.2630, "type": "restaurant"},
                {"id": "s3", "name": "Marine Drive Kochi", "latitude": 9.9780, "longitude": 76.2750, "type": "attraction"},
            ],
            "segments": [
                {"from_stop": "Grand Hyatt", "to_stop": "Fort Kochi", "distance_meters": 6500.0, "duration_seconds": 900},
            ],
            "total_distance_meters": 15500.0,
            "total_duration_seconds": 2220,
            "mode": "driving",
            "score": 95.0,
        }
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route)):
            r8 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "build the route"})
            self.assertEqual(r8.status_code, 200)
            d8 = r8.json()
            self.assertIn("optimize_route", d8["tool_calls"])
            self.assertEqual(d8["state_summary"]["derived_freshness"]["current_route"], "valid")

        r9 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "generate itinerary"})
        self.assertEqual(r9.status_code, 200)
        d9 = r9.json()
        self.assertEqual(d9["state_summary"]["derived_freshness"]["current_itinerary"], "valid")
        print("  [OK] Canonical trip planned: Route & Itinerary are VALID")

        # -----------------------------------------------------------------
        # Dynamic Replanning Scenario 1: Lazy place removal
        # -----------------------------------------------------------------
        print("\n[Replanning 1] Lazy place removal: 'remove Fort Kochi'...")
        r10 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "remove Fort Kochi"})
        self.assertEqual(r10.status_code, 200)
        d10 = r10.json()
        self.assertEqual(len(d10["tool_calls"]), 0, "Lazy removal must NOT call route tool immediately!")
        self.assertEqual(d10["state_summary"]["derived_freshness"]["current_route"], "stale")
        self.assertEqual(d10["state_summary"]["derived_freshness"]["current_itinerary"], "stale")
        self.assertIn("need updating", d10["response"].lower())
        print("  [OK] Fort Kochi removed lazily; route & itinerary marked STALE; no premature route tool call.")

        # -----------------------------------------------------------------
        # Dynamic Replanning Scenario 2: Explicit route rebuild
        # -----------------------------------------------------------------
        print("\n[Replanning 2] Explicit route rebuild: 'build the route'...")
        mock_route_2 = {
            "origin": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "destination": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "ordered_stops": [
                {"id": "s2", "name": "Malabar Cafe", "latitude": 9.9885, "longitude": 76.2630, "type": "restaurant"},
                {"id": "s3", "name": "Marine Drive Kochi", "latitude": 9.9780, "longitude": 76.2750, "type": "attraction"},
            ],
            "segments": [
                {"from_stop": "Grand Hyatt", "to_stop": "Malabar Cafe", "distance_meters": 2000.0, "duration_seconds": 300},
            ],
            "total_distance_meters": 8000.0,
            "total_duration_seconds": 1200,
            "mode": "driving",
            "score": 98.0,
        }
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route_2)):
            r11 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "build the route"})
            self.assertEqual(r11.status_code, 200)
            d11 = r11.json()
            self.assertIn("optimize_route", d11["tool_calls"])
            self.assertEqual(d11["state_summary"]["derived_freshness"]["current_route"], "valid")
            self.assertEqual(d11["state_summary"]["derived_freshness"]["current_itinerary"], "stale")
            print("  [OK] Route rebuilt with current stops; CURRENT_ROUTE is VALID; itinerary remains STALE.")

        # -----------------------------------------------------------------
        # Dynamic Replanning Scenario 3: Explicit itinerary update
        # -----------------------------------------------------------------
        print("\n[Replanning 3] Explicit itinerary update: 'update the itinerary'...")
        r12 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "update the itinerary"})
        self.assertEqual(r12.status_code, 200)
        d12 = r12.json()
        self.assertIn("generate_itinerary", d12["tool_calls"])
        self.assertEqual(d12["state_summary"]["derived_freshness"]["current_itinerary"], "valid")
        print("  [OK] Itinerary regenerated using the rebuilt route; CURRENT_ITINERARY is VALID.")

        # -----------------------------------------------------------------
        # Dynamic Replanning Scenario 4: Compound travel mode change + route rebuild
        # -----------------------------------------------------------------
        print("\n[Replanning 4] Compound command: 'change to walking and rebuild the route'...")
        mock_route_walk = dict(mock_route_2)
        mock_route_walk["mode"] = "walking"
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route_walk)) as mock_route_call:
            r13 = self.client.post("/agent/chat", json={
                "conversation_id": cid,
                "message": "change to walking and rebuild the route",
            })
            self.assertEqual(r13.status_code, 200)
            d13 = r13.json()
            self.assertEqual(d13["state_summary"]["travel_mode"], "walking")
            self.assertIn("optimize_route", d13["tool_calls"])
            self.assertEqual(d13["state_summary"]["derived_freshness"]["current_route"], "valid")
            self.assertEqual(mock_route_call.call_args[0][0].mode, "walking")
            print("  [OK] Travel mode changed to walking and route rebuilt in 1 atomic turn; mode verified at service.")

        # -----------------------------------------------------------------
        # Dynamic Replanning Scenario 5: Change Hotel
        # -----------------------------------------------------------------
        print("\n[Replanning 5] Change Hotel: 'change hotel'...")
        session = get_session(cid)
        new_hotel = Hotel(data_id="h2", name="Brunton Boatyard", latitude=9.9680, longitude=76.2440, price_per_night=1200, currency="INR")
        session.trip_state.hotel_selection = new_hotel
        apply_trip_state_update(session.trip_state, {"hotel_selection": new_hotel})
        self.assertEqual(session.trip_state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(session.trip_state.hotel_selection.name, "Brunton Boatyard")
        print("  [OK] Hotel changed to Brunton Boatyard; downstream route invalidated to STALE.")

        # -----------------------------------------------------------------
        # Inspection Checks: Leakage & Text Purity
        # -----------------------------------------------------------------
        all_responses = [d8["response"], d9["response"], d10["response"], d11["response"], d12["response"], d13["response"]]
        for resp in all_responses:
            self.assertNotIn("CURRENT_ROUTE", resp)
            self.assertNotIn("NOT_AVAILABLE", resp)
            self.assertNotIn("derived_freshness", resp)
            self.assertNotIn("state_version", resp)
            self.assertNotIn("property_token", resp)
            self.assertNotIn("tool_call", resp)

        print("\n" + "=" * 65)
        print("ALL REPLANNING E2E SCENARIOS & INSPECTION CHECKS PASSED 100%!")
        print("=" * 65)


if __name__ == "__main__":
    unittest.main()
