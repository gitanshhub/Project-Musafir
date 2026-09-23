"""
Project Musafir — Step 12.5 Test Suite: Deterministic State Engine,
Dependency Invalidation, Stale/Race Guards, and Performance Instrumentation.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import date

from app.agent.state import TripState
from app.agent.state_update import (
    TripChangeSet,
    apply_trip_state_update,
    check_trip_readiness,
    get_next_missing_requirement,
    is_broad_destination,
)
from app.agent.tools import update_trip_state_tool, execute_tool
from app.agent.loop import AgentLoop, AgentResult
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.route import OptimizedRoute, LocationPoint
from app.schemas.itinerary import ItineraryResponse, DailyItinerary


class TestStep12_5StateEngine(unittest.TestCase):
    """Tests for deterministic state mutations and dependency invalidations."""

    def test_01_pure_state_mutation(self):
        """User sets initial trip parameters."""
        state = TripState()
        self.assertEqual(state.state_version, 1)

        change_set = apply_trip_state_update(state, {
            "destination": "Jaipur",
            "number_of_days": 3,
            "hotel_budget": 5000.0,
            "travel_mode": "transit",
            "interests": ["heritage", "forts"],
        })

        self.assertTrue(change_set.has_changes())
        self.assertIn("destination", change_set.changed_fields)
        self.assertIn("number_of_days", change_set.changed_fields)
        self.assertIn("hotel_budget", change_set.changed_fields)
        self.assertIn("travel_mode", change_set.changed_fields)
        self.assertIn("interests", change_set.changed_fields)
        self.assertEqual(change_set.invalidated_fields, [])

        self.assertEqual(state.destination, "Jaipur")
        self.assertEqual(state.number_of_days, 3)
        self.assertEqual(state.hotel_budget, 5000.0)
        self.assertEqual(state.travel_mode, "transit")
        self.assertEqual(state.state_version, 2)

    def test_02_destination_change_invalidates_dependent_data(self):
        """Golden Test A: Jaipur -> Kashmir preserves duration & budget, invalidates destination-specific data."""
        state = TripState(
            destination="Jaipur",
            number_of_days=3,
            hotel_budget=20000.0,
            interests=["history"],
            state_version=2,
            hotel_selection=Hotel(
                name="Jaipur Palace",
                rating=4.5,
                review_count=100,
                price_per_night=4500.0,
                latitude=26.9,
                longitude=75.8,
            ),
            selected_places=[
                Place(
                    name="Amber Fort",
                    latitude=26.9855,
                    longitude=75.8513,
                    rating=4.6,
                    data_id="amber_fort_001",
                )
            ],
            current_route=OptimizedRoute(
                ordered_stops=[],
                segments=[],
                total_distance_meters=10000.0,
                total_duration_seconds=1800.0,
                score=0.9,
            ),
        )

        change_set = apply_trip_state_update(state, {
            "destination": "Kashmir",
        })

        # Destination changed
        self.assertEqual(state.destination, "Kashmir")
        self.assertIn("destination", change_set.changed_fields)

        # Destination-specific data invalidated
        self.assertIn("selected_hotel", change_set.invalidated_fields)
        self.assertIn("selected_places", change_set.invalidated_fields)
        self.assertIn("current_route", change_set.invalidated_fields)
        self.assertIsNone(state.hotel_selection)
        self.assertEqual(state.selected_places, [])
        self.assertIsNone(state.current_route)

        # Non-dependent data preserved
        self.assertEqual(state.number_of_days, 3)
        self.assertEqual(state.hotel_budget, 20000.0)
        self.assertEqual(state.interests, ["history"])
        self.assertEqual(state.state_version, 3)

    def test_03_budget_increase_preserves_selected_hotel(self):
        """Refinement 6: Increasing budget keeps the existing hotel intact."""
        state = TripState(
            destination="Jaipur",
            hotel_budget=5000.0,
            hotel_selection=Hotel(
                name="Heritage Haveli",
                price_per_night=4000.0,
                latitude=26.9,
                longitude=75.8,
            ),
            state_version=2,
        )

        change_set = apply_trip_state_update(state, {"hotel_budget": 8000.0})

        self.assertEqual(state.hotel_budget, 8000.0)
        self.assertNotIn("selected_hotel", change_set.invalidated_fields)
        self.assertIsNotNone(state.hotel_selection)
        self.assertEqual(state.hotel_selection.name, "Heritage Haveli")

    def test_04_budget_decrease_below_hotel_rate_invalidates_hotel(self):
        """Refinement 6: Decreasing budget below hotel rate invalidates it."""
        state = TripState(
            destination="Jaipur",
            hotel_budget=5000.0,
            hotel_selection=Hotel(
                name="Luxury Fort Resort",
                price_per_night=4800.0,
                latitude=26.9,
                longitude=75.8,
            ),
            state_version=2,
        )

        change_set = apply_trip_state_update(state, {"hotel_budget": 3500.0})

        self.assertEqual(state.hotel_budget, 3500.0)
        self.assertIn("selected_hotel", change_set.invalidated_fields)
        self.assertIsNone(state.hotel_selection)

    def test_05_duration_and_mode_change_invalidates_itinerary_and_route(self):
        """Duration change invalidates itinerary; mode change invalidates route and itinerary."""
        state = TripState(
            destination="Jaipur",
            number_of_days=3,
            travel_mode="driving",
            current_route=OptimizedRoute(
                ordered_stops=[],
                segments=[],
                total_distance_meters=15000.0,
                total_duration_seconds=2700.0,
                score=0.9,
            ),
            current_itinerary=ItineraryResponse(
                days=[DailyItinerary(day_number=1, date=date(2026, 4, 1), start_time="09:00", end_time="18:00")],
                total_days=1,
                total_distance_meters=1000.0,
                total_travel_seconds=600.0,
                total_visit_minutes=120,
            ),
            state_version=2,
        )

        change_set = apply_trip_state_update(state, {
            "number_of_days": 5,
            "travel_mode": "walking",
        })

        self.assertEqual(state.number_of_days, 5)
        self.assertEqual(state.travel_mode, "walking")
        self.assertIn("current_itinerary", change_set.invalidated_fields)
        self.assertIn("current_route", change_set.invalidated_fields)
        self.assertIsNone(state.current_itinerary)
        self.assertIsNone(state.current_route)

    def test_06_broad_destination_distinction(self):
        """Refinement 3: 'South India' requires clarification, while 'Kashmir' is valid."""
        self.assertTrue(is_broad_destination("South India"))
        self.assertTrue(is_broad_destination("north india"))
        self.assertFalse(is_broad_destination("Kashmir"))
        self.assertFalse(is_broad_destination("Jaipur"))
        self.assertFalse(is_broad_destination("Goa"))
        self.assertFalse(is_broad_destination("Kerala"))

    def test_07_progressive_interviewing_readiness_engine(self):
        """Readiness and missing requirement detection."""
        state = TripState()
        self.assertEqual(get_next_missing_requirement(state), "destination")

        state.destination = "South India"
        self.assertEqual(get_next_missing_requirement(state), "destination_clarification")

        state.destination = "Kashmir"
        self.assertEqual(get_next_missing_requirement(state), "duration")

        state.number_of_days = 4
        self.assertEqual(get_next_missing_requirement(state), "hotel_budget")

        state.hotel_budget = 4000.0
        self.assertEqual(get_next_missing_requirement(state), "interests")

        state.interests = ["nature", "valleys"]
        self.assertIsNone(get_next_missing_requirement(state))

        readiness = check_trip_readiness(state)
        self.assertTrue(readiness["has_destination"])
        self.assertTrue(readiness["ready_for_hotel_search"])
        self.assertTrue(readiness["ready_for_place_search"])

    def test_08_update_trip_state_tool_execution(self):
        """update_trip_state tool modifies state in-place and returns structured change set."""
        state = TripState()
        result = update_trip_state_tool(
            trip_state=state,
            destination="Kashmir",
            number_of_days=5,
            hotel_budget=6000.0,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["state_version"], 2)
        self.assertEqual(state.destination, "Kashmir")
        self.assertEqual(state.number_of_days, 5)
        self.assertEqual(state.hotel_budget, 6000.0)
        self.assertEqual(result["next_missing"], "interests")


class TestStep12_5AgentLoopProtection(unittest.TestCase):
    """Tests for race-condition guards, destination context guards, and performance instrumentation."""

    def test_09_stale_destination_tool_call_is_discarded(self):
        """Refinement 10: Tool call for Jaipur when current state is Kashmir is discarded."""
        state = TripState(destination="Kashmir", state_version=5)

        # Mock LLM calling search_hotels for Jaipur
        mock_client = MagicMock()
        mock_client.send_message.side_effect = [
            MagicMock(
                content="",
                tool_calls=[{
                    "id": "call_123",
                    "function": {
                        "name": "search_hotels",
                        "arguments": '{"destination": "Jaipur"}',
                    },
                }],
            ),
            MagicMock(
                content="Understood. Searching for Kashmir accommodations.",
                tool_calls=None,
            ),
        ]

        # Tool executor returning Jaipur hotels
        mock_executor = MagicMock(return_value={
            "success": True,
            "hotels": [{"name": "Jaipur Hotel", "price_per_night": 3000}],
        })

        loop = AgentLoop(client=mock_client, tool_executor=mock_executor)
        result = loop.run(user_message="Find hotels", trip_state=state)

        # The tool executor should NOT have been called for Jaipur, or result discarded
        self.assertIsNone(result.results)
        self.assertIn("search_hotels", result.tool_calls)

    def test_10_stale_state_version_tool_call_is_discarded(self):
        """Refinement 7: State version change between request and completion discards tool result."""
        state = TripState(destination="Jaipur", state_version=2)

        # Simulate concurrent state update during tool execution
        def side_effect_tool(name, args, **kwargs):
            # Mutate state version concurrently
            state.state_version = 3
            state.destination = "Kashmir"
            return {
                "success": True,
                "hotels": [{"name": "Jaipur Heritage Hotel", "price_per_night": 4000}],
            }

        mock_client = MagicMock()
        mock_client.send_message.side_effect = [
            MagicMock(
                content="",
                tool_calls=[{
                    "id": "call_456",
                    "function": {
                        "name": "search_hotels",
                        "arguments": '{"destination": "Jaipur"}',
                    },
                }],
            ),
            MagicMock(
                content="State was updated to Kashmir.",
                tool_calls=None,
            ),
        ]

        loop = AgentLoop(client=mock_client, tool_executor=side_effect_tool)
        result = loop.run(user_message="Search hotels", trip_state=state)

        # Hotel results from stale state version must be discarded
        self.assertIsNone(result.results)

    def test_11_performance_metrics_instrumentation(self):
        """Refinement 8: Performance metrics captured in AgentResult."""
        state = TripState(destination="Jaipur", state_version=1)

        mock_client = MagicMock()
        mock_client.send_message.return_value = MagicMock(
            content="Welcome to Jaipur!",
            tool_calls=None,
        )

        loop = AgentLoop(client=mock_client)
        result = loop.run(user_message="Hello", trip_state=state)

        self.assertIsNotNone(result.metrics)
        self.assertIn("total_turn_ms", result.metrics)
        self.assertIn("total_llm_ms", result.metrics)
        self.assertIn("total_tool_ms", result.metrics)
        self.assertIn("total_state_update_ms", result.metrics)
        self.assertEqual(result.metrics["llm_iterations"], 1)


if __name__ == "__main__":
    unittest.main()
