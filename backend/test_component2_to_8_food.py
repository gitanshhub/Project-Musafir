"""
Project Musafir — Milestone 2 Verification Suite
Tests Components 2 through 8:
- Food search domain models and query parameter construction
- Anchored restaurant & cafe search service (hotel, place, route anchors, ll coords)
- Exclusion of rejected restaurants & cafes
- Food intent resolution (fast-path + planner)
- Single and multi-item food selection without premature route optimization
- Food rejection and route invalidation
"""

import unittest
from unittest.mock import patch, MagicMock

from app.agent.state import TripState, PlanningStage
from app.agent.context import SessionState, VisibleItemReference
from app.agent.fast_path import resolve_fast_path
from app.agent.planner import decide_next_planning_action, PlanningActionType
from app.agent.state_update import apply_trip_state_update
from app.agent.tools import search_restaurants_tool, optimize_route_tool
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant, FoodSearchRequest, FoodAnchorType, FoodCategory
from app.schemas.route import OptimizedRoute, RouteSegment
from app.services.restaurant_service import build_restaurant_search_params


class TestMilestone2FoodIntegration(unittest.TestCase):

    def setUp(self):
        self.state = TripState(
            destination="Kerala",
            number_of_days=5,
            number_of_nights=4,
            hotel_selection=Hotel(
                name="Grand Hyatt Kochi Bolgatty",
                latitude=9.9880,
                longitude=76.2625,
                rating=4.7,
                review_count=3500,
                price_per_night=8500.0,
                property_token="gh_kochi_123",
            ),
            selected_places=[
                Place(name="Fort Kochi", latitude=9.9658, longitude=76.2421, data_id="fort_kochi_1"),
                Place(name="Marine Drive Kochi", latitude=9.9780, longitude=76.2750, data_id="marine_drive_2"),
            ],
            planning_stage=PlanningStage.PLACE_SELECTION,
        )
        self.session = SessionState(conversation_id="conv_m2_123", trip_state=self.state)

    def test_01_build_restaurant_search_params_anchors_and_coords(self):
        """Test build_restaurant_search_params properly generates query and ll center coords."""
        # Case A: Anchored near hotel with coords
        params = build_restaurant_search_params(
            destination="Kerala",
            location_anchor="Grand Hyatt Kochi Bolgatty",
            latitude=9.9880,
            longitude=76.2625,
            meal_type="lunch",
            dietary="vegetarian",
            price_level="cheap",
        )
        self.assertEqual(params["engine"], "google_maps")
        self.assertEqual(params["ll"], "@9.988,76.2625,15z")
        self.assertIn("vegetarian", params["q"].lower())
        self.assertIn("cheap", params["q"].lower())
        self.assertIn("lunch", params["q"].lower())
        self.assertIn("grand hyatt", params["q"].lower())

        # Case B: Cafe search
        params_cafe = build_restaurant_search_params(
            destination="Kerala",
            category="cafe",
            location_anchor="Fort Kochi",
            latitude=9.9658,
            longitude=76.2421,
        )
        self.assertIn("cafes near fort kochi", params_cafe["q"].lower())
        self.assertEqual(params_cafe["ll"], "@9.9658,76.2421,15z")

    def test_02_search_restaurants_tool_anchoring_and_exclusions(self):
        """Test search_restaurants_tool inherits anchor from state and filters rejected food."""
        mock_raw = {
            "local_results": [
                {
                    "title": "Malabar Cafe",
                    "data_id": "malabar_cafe_1",
                    "rating": 4.6,
                    "reviews": 1200,
                    "address": "Bolgatty Island, Kochi",
                    "gps_coordinates": {"latitude": 9.9885, "longitude": 76.2630},
                    "type": "Cafe",
                },
                {
                    "title": "Bolgatty Palace Restaurant",
                    "data_id": "bolgatty_rest_2",
                    "rating": 4.2,
                    "reviews": 850,
                    "address": "Bolgatty, Kochi",
                    "gps_coordinates": {"latitude": 9.9890, "longitude": 76.2640},
                    "type": "Restaurant",
                },
                {
                    "title": "Rejected Bistro",
                    "data_id": "rejected_bistro_3",
                    "rating": 3.8,
                    "reviews": 100,
                    "address": "Kochi",
                    "gps_coordinates": {"latitude": 9.9800, "longitude": 76.2600},
                    "type": "Restaurant",
                }
            ]
        }

        # Set rejected restaurant in state
        self.state.rejected_restaurants = ["Rejected Bistro"]

        with patch("app.agent.tools.fetch_restaurants_from_serpapi", return_value=mock_raw) as mock_fetch:
            res = search_restaurants_tool(
                destination="Kerala",
                query="lunch near hotel",
                limit=5,
                api_key="mock_key",
                trip_state=self.state,
            )
            self.assertTrue(res["success"])
            # Verified mock_fetch received hotel coords
            call_params = mock_fetch.call_args[0][0]
            self.assertEqual(call_params["ll"], "@9.988,76.2625,15z")
            self.assertIn("grand hyatt kochi bolgatty", call_params["q"].lower())

            # Verified 'Rejected Bistro' was excluded
            returned_names = [r["name"] for r in res["restaurants"]]
            self.assertIn("Malabar Cafe", returned_names)
            self.assertIn("Bolgatty Palace Restaurant", returned_names)
            self.assertNotIn("Rejected Bistro", returned_names)

    def test_03_fast_path_food_intent_resolution(self):
        """Test FastPath detects food intent and extracts parameters without LLM roundtrip."""
        # 1. Lunch near route
        res1 = resolve_fast_path("find lunch near route", self.session)
        self.assertTrue(res1.matched)
        self.assertEqual(res1.intent, "SEARCH_FOOD")
        self.assertEqual(res1.reference_resolution["meal_type"], "lunch")
        self.assertEqual(res1.reference_resolution["anchor"], "route")

        # 2. Show cafes near hotel
        res2 = resolve_fast_path("show cafes near the hotel", self.session)
        self.assertTrue(res2.matched)
        self.assertEqual(res2.intent, "SEARCH_FOOD")
        self.assertEqual(res2.reference_resolution["category"], "cafe")
        self.assertEqual(res2.reference_resolution["anchor"], "hotel")

        # 3. Find dinner
        res3 = resolve_fast_path("find dinner", self.session)
        self.assertTrue(res3.matched)
        self.assertEqual(res3.intent, "SEARCH_FOOD")
        self.assertEqual(res3.reference_resolution["meal_type"], "dinner")

    def test_04_food_selection_and_multi_selection(self):
        """Test selecting food stops moves to FOOD_SELECTION and does NOT optimize route prematurely."""
        # Populate visible restaurants on screen
        self.session.conversation_context.set_visible_items("restaurant", [
            {"name": "Seagull Restaurant", "data_id": "r1", "category": "Restaurant", "rating": 4.5},
            {"name": "Kashi Art Cafe", "data_id": "r2", "category": "Cafe", "rating": 4.7},
            {"name": "Dhe Puttu", "data_id": "r3", "category": "Restaurant", "rating": 4.3},
        ])
        self.state.planning_stage = PlanningStage.FOOD_SELECTION

        # Multi-selection: "first and second"
        res_multi = resolve_fast_path("take the first and second", self.session)
        self.assertTrue(res_multi.matched)
        self.assertEqual(res_multi.intent, "SELECT_ITEMS")
        self.assertIn("selected_restaurants", res_multi.state_updates)
        self.assertIn("selected_cafes", res_multi.state_updates)

        # Apply updates
        apply_trip_state_update(self.state, res_multi.state_updates)
        self.assertEqual(len(self.state.selected_restaurants), 1)
        self.assertEqual(self.state.selected_restaurants[0].name, "Seagull Restaurant")
        self.assertEqual(len(self.state.selected_cafes), 1)
        self.assertEqual(self.state.selected_cafes[0].name, "Kashi Art Cafe")
        self.assertEqual(self.state.planning_stage, PlanningStage.FOOD_SELECTION)

        # Verify planner preserves FOOD_SELECTION and does NOT optimize route
        action = decide_next_planning_action(self.state, self.session.conversation_context, user_intent="SELECT_ITEMS")
        self.assertEqual(action.action_type, PlanningActionType.WAIT_FOR_FOOD_SELECTION)
        self.assertEqual(action.target_stage, PlanningStage.FOOD_SELECTION)

    def test_05_food_rejection_and_route_invalidation(self):
        """Test rejecting food removes it and invalidates any existing route."""
        # Set a mock current route
        self.state.current_route = OptimizedRoute(
            origin={"name": "Grand Hyatt", "latitude": 9.988, "longitude": 76.262},
            destination={"name": "Grand Hyatt", "latitude": 9.988, "longitude": 76.262},
            ordered_stops=[],
            segments=[],
            total_distance_meters=15000.0,
            total_duration_seconds=1800,
            mode="driving",
            score=95.0,
        )
        self.state.selected_restaurants = [
            Restaurant(name="Seagull Restaurant", data_id="r1", latitude=9.96, longitude=76.24)
        ]
        self.state.planning_stage = PlanningStage.FOOD_SELECTION

        # Rejection: "remove Seagull Restaurant"
        res_rej = resolve_fast_path("remove Seagull Restaurant", self.session)
        self.assertTrue(res_rej.matched)
        self.assertEqual(res_rej.intent, "REJECT_ITEM")
        self.assertIn("rejected_restaurants", res_rej.state_updates)

        # Apply rejection
        apply_trip_state_update(self.state, res_rej.state_updates)
        self.assertEqual(len(self.state.selected_restaurants), 0)
        self.assertTrue(any(r.lower() == "seagull restaurant" for r in self.state.rejected_restaurants))
        # Route MUST be invalidated
        self.assertIsNone(self.state.current_route)

    def test_06_route_request_integrates_food_stops(self):
        """Test explicit route request includes both places and food stops in optimize_route tool args."""
        self.state.selected_restaurants = [
            Restaurant(name="Malabar Cafe", data_id="r1", latitude=9.9885, longitude=76.2630)
        ]
        action = decide_next_planning_action(self.state, self.session.conversation_context, user_intent="ROUTE_REQUEST")
        self.assertEqual(action.action_type, PlanningActionType.OPTIMIZE_ROUTE)
        self.assertEqual(action.tool_name, "optimize_route")
        stops = action.tool_args["stops"]
        # 2 places + 1 restaurant = 3 stops
        self.assertEqual(len(stops), 3)
        stop_names = [s["name"] for s in stops]
        self.assertIn("Fort Kochi", stop_names)
        self.assertIn("Marine Drive Kochi", stop_names)
        self.assertIn("Malabar Cafe", stop_names)


if __name__ == "__main__":
    unittest.main()
