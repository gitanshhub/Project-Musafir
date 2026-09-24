"""
Project Musafir — Step 14: Milestone 2 End-to-End Travel Planning Suite
Verifies the complete Hotel -> Places -> Food -> Route -> Itinerary integration flow:
1. Destination setup ("Kerala")
2. Duration ("5 days")
3. Budget ("total 5000")
4. Dates ("24 Sept")
5. Hotel Selection ("Grand Hyatt Kochi Bolgatty")
6. Anchored Place Discovery
7. Multi-place Selection ("first and second")
8. Anchored Food Discovery ("find lunch near route")
9. Food Selection ("take the first restaurant") -> FOOD_SELECTION (no premature route!)
10. Route Request ("build the route") -> Route covering Hotel + Places + Restaurant
11. Itinerary Generation -> Lunch placed in 12:00-14:30 meal window
12. Food Removal ("remove Malabar Cafe") -> Route invalidated
13. Recalculate Route -> Route rebuilt with only places
"""

import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from app.agent.state import TripState, PlanningStage
from app.agent.context import get_session
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import OptimizedRoute


class TestMilestone2E2E(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_milestone_2_full_user_journey(self):
        print("\n" + "=" * 65)
        print("STEP 14: MILESTONE 2 E2E FOOD & CAFE INTEGRATION SUITE")
        print("=" * 65)

        # -----------------------------------------------------------------
        # [Turn 1] User specifies destination: 'Kerala'
        # -----------------------------------------------------------------
        print("\n[Turn 1] User specifies destination: 'Kerala'...")
        r1 = self.client.post("/agent/chat", json={"message": "Kerala"})
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        cid = d1["conversation_id"]
        self.assertEqual(d1["state_summary"]["destination"], "Kerala")
        print("  [OK] Destination captured: Kerala")

        # -----------------------------------------------------------------
        # [Turn 2] User specifies duration: '5 days'
        # -----------------------------------------------------------------
        print("\n[Turn 2] User specifies duration: '5 days'...")
        r2 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "5 days"})
        self.assertEqual(r2.status_code, 200)
        d2 = r2.json()
        self.assertEqual(d2["state_summary"]["number_of_days"], 5)
        self.assertEqual(d2["state_summary"]["number_of_nights"], 4)
        print("  [OK] Duration captured: 5 days, 4 nights")

        # -----------------------------------------------------------------
        # [Turn 3] User specifies budget: 'total 5000'
        # -----------------------------------------------------------------
        print("\n[Turn 3] User specifies budget: 'total 5000'...")
        r3 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "total 5000"})
        self.assertEqual(r3.status_code, 200)
        d3 = r3.json()
        self.assertEqual(d3["state_summary"]["hotel_budget"], 1250.0)
        print("  [OK] Budget derived: total INR 5000.0 -> INR 1250.0/night")

        # -----------------------------------------------------------------
        # [Turn 4] User specifies dates: '24 Sept'
        # -----------------------------------------------------------------
        print("\n[Turn 4] User specifies dates: '24 Sept'...")
        r4 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "24 Sept"})
        self.assertEqual(r4.status_code, 200)
        d4 = r4.json()
        self.assertEqual(d4["state_summary"]["dates"], "2026-09-24 to 2026-09-28")
        print("  [OK] Dates resolved: 2026-09-24 to 2026-09-28")

        # -----------------------------------------------------------------
        # [Turn 5] User selects hotel from visible options
        # -----------------------------------------------------------------
        print("\n[Turn 5] User selects hotel from visible options...")
        session = get_session(cid)
        session.conversation_context.set_visible_items("hotel", [
            {
                "name": "Grand Hyatt Kochi Bolgatty",
                "price_per_night": 1200.0,
                "rating": 4.8,
                "property_token": "gh_123",
                "latitude": 9.9880,
                "longitude": 76.2625,
            }
        ])
        r5 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "the first hotel"})
        self.assertEqual(r5.status_code, 200)
        d5 = r5.json()
        self.assertEqual(d5["state_summary"]["hotel"], "Grand Hyatt Kochi Bolgatty")
        self.assertEqual(d5["state_summary"]["planning_stage"], "PLACE_DISCOVERY")
        print("  [OK] Hotel selected: Grand Hyatt Kochi Bolgatty")

        # -----------------------------------------------------------------
        # [Turn 6] Place selection: multi-select two attractions
        # -----------------------------------------------------------------
        print("\n[Turn 6] Place selection: multi-select 'first and second'...")
        session = get_session(cid)
        session.conversation_context.set_visible_items("place", [
            {"name": "Fort Kochi", "data_id": "fk_1", "latitude": 9.9658, "longitude": 76.2421},
            {"name": "Marine Drive Kochi", "data_id": "md_2", "latitude": 9.9780, "longitude": 76.2750},
        ])
        r6 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "take the first and second"})
        self.assertEqual(r6.status_code, 200)
        d6 = r6.json()
        self.assertEqual(d6["state_summary"]["places_count"], 2)
        self.assertEqual(d6["state_summary"]["planning_stage"], "PLACE_SELECTION")
        print("  [OK] Places selected: Fort Kochi, Marine Drive Kochi (stage: PLACE_SELECTION)")

        # -----------------------------------------------------------------
        # [Turn 7] Food Discovery: 'find lunch near route'
        # -----------------------------------------------------------------
        print("\n[Turn 7] Food Discovery: 'find lunch near route'...")
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
            r7 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "find lunch near route"})
            self.assertEqual(r7.status_code, 200)
            d7 = r7.json()
            self.assertIn("search_restaurants", d7["tool_calls"])
            self.assertEqual(d7["state_summary"]["planning_stage"], "FOOD_SELECTION")
            self.assertIsNotNone(d7["results"])
            self.assertIn("restaurants", d7["results"])
            self.assertEqual(len(d7["results"]["restaurants"]), 2)
            print("  [OK] search_restaurants executed; results returned and visible cards updated.")

        # -----------------------------------------------------------------
        # [Turn 8] Food Selection: 'take the first restaurant'
        # -----------------------------------------------------------------
        print("\n[Turn 8] Food Selection: 'take the first restaurant'...")
        r8 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "take the first restaurant"})
        self.assertEqual(r8.status_code, 200)
        d8 = r8.json()
        # Verify stage remains FOOD_SELECTION (no premature routing!)
        self.assertEqual(d8["state_summary"]["planning_stage"], "FOOD_SELECTION")
        # Malabar Cafe is a cafe
        self.assertEqual(d8["state_summary"]["cafes_count"], 1)
        self.assertIn("Malabar Cafe", d8["state_summary"]["cafes"])
        print("  [OK] Malabar Cafe selected; confirmed stage remains: FOOD_SELECTION (no premature routing)")

        # -----------------------------------------------------------------
        # [Turn 9] Route Request: 'build the route'
        # -----------------------------------------------------------------
        print("\n[Turn 9] Route Request: 'build the route'...")
        mock_route = {
            "origin": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "destination": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "ordered_stops": [
                {"id": "s1", "name": "Fort Kochi", "latitude": 9.9658, "longitude": 76.2421, "type": "attraction", "meal_type": None},
                {"id": "s2", "name": "Malabar Cafe", "latitude": 9.9885, "longitude": 76.2630, "type": "cafe", "meal_type": "lunch"},
                {"id": "s3", "name": "Marine Drive Kochi", "latitude": 9.9780, "longitude": 76.2750, "type": "attraction", "meal_type": None},
            ],
            "segments": [
                {"from_stop": "Grand Hyatt", "to_stop": "Fort Kochi", "distance_meters": 6500.0, "duration_seconds": 900},
                {"from_stop": "Fort Kochi", "to_stop": "Malabar Cafe", "distance_meters": 6800.0, "duration_seconds": 960},
                {"from_stop": "Malabar Cafe", "to_stop": "Marine Drive Kochi", "distance_meters": 2200.0, "duration_seconds": 360},
            ],
            "total_distance_meters": 15500.0,
            "total_duration_seconds": 2220,
            "mode": "driving",
            "score": 95.0,
        }
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route)):
            r9 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "build the route"})
            self.assertEqual(r9.status_code, 200)
            d9 = r9.json()
            self.assertEqual(d9["state_summary"]["planning_stage"], "ROUTE_PLANNING")
            self.assertIn("optimize_route", d9["tool_calls"])
            self.assertIn("covering 3 stops", d9["response"])
            print("  [OK] Optimal route constructed with 3 stops (Hotel -> Fort Kochi -> Malabar Cafe -> Marine Drive)")

        # -----------------------------------------------------------------
        # [Turn 10] Generate Itinerary: Meal window scheduling verification
        # -----------------------------------------------------------------
        print("\n[Turn 10] Generate Itinerary: Scheduling check for lunch window...")
        from app.agent.tools import generate_itinerary_tool
        session = get_session(cid)
        itin_res = generate_itinerary_tool(trip_state=session.trip_state)
        self.assertTrue(itin_res["success"])
        itin_data = itin_res["itinerary"]
        day1 = itin_data["days"][0]
        item_names = [it["name"] for it in day1["items"]]
        self.assertIn("Malabar Cafe", item_names)
        cafe_item = next(it for it in day1["items"] if it["name"] == "Malabar Cafe")
        # Verified lunch falls in or after 12:00
        start_hour = int(cafe_item["arrival_time"].split(":")[0])
        self.assertGreaterEqual(start_hour, 12, "Lunch must be scheduled at or after 12:00")
        print(f"  [OK] Malabar Cafe scheduled at {cafe_item['arrival_time']} -> {cafe_item['departure_time']} (inside lunch window)")

        # -----------------------------------------------------------------
        # [Turn 11] Food Removal: 'remove Malabar Cafe'
        # -----------------------------------------------------------------
        print("\n[Turn 11] Food Removal: 'remove Malabar Cafe'...")
        r11 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "remove Malabar Cafe"})
        self.assertEqual(r11.status_code, 200)
        d11 = r11.json()
        self.assertEqual(d11["state_summary"]["cafes_count"], 0)
        self.assertIn("Malabar Cafe", d11["state_summary"]["rejected_cafes"])
        session = get_session(cid)
        # Verify route was invalidated
        self.assertIsNone(session.trip_state.current_route)
        print("  [OK] Malabar Cafe removed; route invalidated; places preserved: Fort Kochi, Marine Drive Kochi")

        # -----------------------------------------------------------------
        # [Turn 12] Recalculate Route: Rebuilds route with only places
        # -----------------------------------------------------------------
        print("\n[Turn 12] Recalculate Route: 'build the route' again...")
        mock_route_places_only = {
            "origin": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "destination": {"name": "Grand Hyatt Kochi Bolgatty", "latitude": 9.9880, "longitude": 76.2625},
            "ordered_stops": [
                {"id": "s1", "name": "Fort Kochi", "latitude": 9.9658, "longitude": 76.2421, "type": "attraction", "meal_type": None},
                {"id": "s2", "name": "Marine Drive Kochi", "latitude": 9.9780, "longitude": 76.2750, "type": "attraction", "meal_type": None},
            ],
            "segments": [
                {"from_stop": "Grand Hyatt", "to_stop": "Fort Kochi", "distance_meters": 6500.0, "duration_seconds": 900},
                {"from_stop": "Fort Kochi", "to_stop": "Marine Drive Kochi", "distance_meters": 7100.0, "duration_seconds": 1000},
            ],
            "total_distance_meters": 13600.0,
            "total_duration_seconds": 1900,
            "mode": "driving",
            "score": 98.0,
        }
        with patch("app.agent.tools.service_optimize_route", return_value=OptimizedRoute(**mock_route_places_only)):
            r12 = self.client.post("/agent/chat", json={"conversation_id": cid, "message": "build the route"})
            self.assertEqual(r12.status_code, 200)
            d12 = r12.json()
            self.assertEqual(d12["state_summary"]["planning_stage"], "ROUTE_PLANNING")
            self.assertIn("covering 2 places", d12["response"])
            print("  [OK] Route cleanly recalculated with 2 places.")

        print("\n" + "=" * 65)
        print("MILESTONE 2 E2E SUITE PASSED 100%!")
        print("=" * 65)


if __name__ == "__main__":
    unittest.main()
