"""
Project Musafir — Milestone 3 Batch 1 Test Suite: Replanning Controller & Lazy/Explicit Replanning
Validates all 20 required behaviors:
1. route stale + explicit route request -> REBUILD_ROUTE
2. itinerary stale + explicit itinerary request -> REBUILD_ITINERARY (when route is valid)
3. valid route + route request does not recompute unnecessarily -> ANSWER
4. NOT_AVAILABLE route + request with prerequisites -> REBUILD_ROUTE
5. missing prerequisites -> ASK
6. lazy mutation does not auto-rebuild -> WAIT (no tool called)
7. explicit mutation + rebuild executes rebuild
8. stale route + itinerary request rebuilds route first (chained route -> itinerary)
9. stale discovery + explicit discovery request -> SEARCH_PLACES / SEARCH_FOOD
10. successful rebuild -> VALID with updated provenance
11. failed rebuild -> remains STALE
12. multiple mutations -> one coherent replan
13. old response rejected by state version
14. replanning loop terminates (finite execution)
15. unrelated user query does not trigger unrelated stale recomputation
16. stale data is never used as current planning input
17. UI/user-facing serialization does not expose internal freshness
18. empty discovery result semantics are consistent
19. all derived resources preserve correct state-version provenance
20. freshness authority is not duplicated
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

from app.agent.dependencies import DerivedResource
from app.agent.freshness import (
    DerivedStateStatus,
    DerivedResourceFreshness,
    FreshnessRegistry,
)
from app.agent.state import TripState, PlanningStage
from app.agent.state_update import apply_trip_state_update, TripChangeSet
from app.agent.replanner import (
    ReplanningActionType,
    ReplanningAction,
    ReplanningController,
    execute_replanning_cycle,
    check_route_prerequisites,
    check_itinerary_prerequisites,
)
from app.agent.loop import sanitize_public_response
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import RouteStop, RouteSegment, OptimizedRoute
from app.schemas.itinerary import ItineraryResponse


def make_test_hotel() -> Hotel:
    return Hotel(
        data_id="h1",
        name="Taj Lake Palace",
        latitude=24.5756,
        longitude=73.6798,
        price_per_night=15000,
        currency="INR",
        rating=4.9,
    )


def make_test_place(data_id: str = "p1", name: str = "City Palace") -> Place:
    return Place(
        data_id=data_id,
        name=name,
        latitude=24.5764,
        longitude=73.6835,
        rating=4.8,
    )


def make_test_restaurant(data_id: str = "r1", name: str = "Ambrai") -> Restaurant:
    return Restaurant(
        data_id=data_id,
        name=name,
        latitude=24.5790,
        longitude=73.6800,
        rating=4.7,
        cuisine=["Rajasthani"],
        price_level="$$",
    )


def make_dummy_route() -> OptimizedRoute:
    stop1 = RouteStop(id="s1", name="Taj Lake Palace", latitude=24.5756, longitude=73.6798, type="hotel")
    stop2 = RouteStop(id="s2", name="City Palace", latitude=24.5764, longitude=73.6835, type="attraction")
    seg = RouteSegment(from_stop="s1", to_stop="s2", distance_meters=2000.0, duration_seconds=400)
    return OptimizedRoute(
        ordered_stops=[stop1, stop2],
        segments=[seg],
        total_distance_meters=2000.0,
        total_duration_seconds=400,
        score=0.95,
    )


def make_dummy_itinerary() -> ItineraryResponse:
    return ItineraryResponse(
        days=[],
        total_days=2,
        total_distance_meters=2000.0,
        total_travel_seconds=400.0,
        total_visit_minutes=60,
    )


class TestBatch1Replanning(unittest.TestCase):
    """Milestone 3 Batch 1: Replanning Controller & Lazy/Explicit Replanning."""

    def setUp(self):
        self.state = TripState(destination="Udaipur", travel_mode="driving", number_of_days=3)
        self.state.hotel_selection = make_test_hotel()
        self.state.selected_places = [make_test_place()]

    # =========================================================================
    # Test 1: route stale + explicit route request -> REBUILD_ROUTE
    # =========================================================================
    def test_01_route_stale_explicit_request(self):
        """When CURRENT_ROUTE is STALE and traveler asks to rebuild route, returns REBUILD_ROUTE."""
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)
        self.assertTrue(self.state.is_derived_stale(DerivedResource.CURRENT_ROUTE))

        action = ReplanningController.decide(
            state=self.state,
            user_intent="ROUTE_REQUEST",
            user_message="rebuild the route",
        )
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ROUTE)
        self.assertEqual(action.target_resource, DerivedResource.CURRENT_ROUTE)
        self.assertTrue(action.prerequisites_met)
        print("  ✓ Test 1 Passed: route stale + explicit route request -> REBUILD_ROUTE")

    # =========================================================================
    # Test 2: itinerary stale + explicit itinerary request -> REBUILD_ITINERARY (when route valid)
    # =========================================================================
    def test_02_itinerary_stale_explicit_request_with_valid_route(self):
        """When route is VALID and itinerary is STALE, requesting itinerary returns REBUILD_ITINERARY."""
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)
        self.state.current_itinerary = make_dummy_itinerary()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ITINERARY)

        action = ReplanningController.decide(
            state=self.state,
            user_intent="ITINERARY_REQUEST",
            user_message="update my itinerary",
        )
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ITINERARY)
        self.assertEqual(action.target_resource, DerivedResource.CURRENT_ITINERARY)
        print("  ✓ Test 2 Passed: itinerary stale + explicit itinerary request -> REBUILD_ITINERARY")

    # =========================================================================
    # Test 3: valid route + route request does not recompute unnecessarily -> ANSWER
    # =========================================================================
    def test_03_valid_route_no_unnecessary_recompute(self):
        """When CURRENT_ROUTE is already VALID, asking to show/build route returns ANSWER without recomputing."""
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)

        action = ReplanningController.decide(
            state=self.state,
            user_intent="ROUTE_REQUEST",
            user_message="show me the route",
        )
        self.assertEqual(action.action_type, ReplanningActionType.ANSWER)
        self.assertEqual(action.reason, "route_already_valid")
        print("  ✓ Test 3 Passed: valid route does not recompute unnecessarily -> ANSWER")

    # =========================================================================
    # Test 4: NOT_AVAILABLE route + request with prerequisites -> REBUILD_ROUTE
    # =========================================================================
    def test_04_not_available_route_with_prerequisites(self):
        """When CURRENT_ROUTE is NOT_AVAILABLE but prerequisites exist, asking to build route returns REBUILD_ROUTE."""
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.NOT_AVAILABLE)
        action = ReplanningController.decide(
            state=self.state,
            user_intent="ROUTE_REQUEST",
            user_message="build the route",
        )
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ROUTE)
        self.assertTrue(action.prerequisites_met)
        print("  ✓ Test 4 Passed: NOT_AVAILABLE route + request with prerequisites -> REBUILD_ROUTE")

    # =========================================================================
    # Test 5: missing prerequisites -> ASK
    # =========================================================================
    def test_05_missing_prerequisites_prompts_user(self):
        """If prerequisites are missing (e.g. no hotel or no places), asking for route returns ASK."""
        empty_state = TripState(destination="Udaipur")
        action = ReplanningController.decide(
            state=empty_state,
            user_intent="ROUTE_REQUEST",
            user_message="build route",
        )
        self.assertEqual(action.action_type, ReplanningActionType.ASK)
        self.assertFalse(action.prerequisites_met)
        self.assertIn("hotel_selection", action.missing_prerequisites)
        self.assertIsNotNone(action.prompt_message)
        print("  ✓ Test 5 Passed: missing prerequisites -> ASK")

    # =========================================================================
    # Test 6: lazy mutation does not auto-rebuild -> WAIT
    # =========================================================================
    def test_06_lazy_mutation_waits(self):
        """Mutating state without explicit replan intent marks resources STALE and returns WAIT (no route call)."""
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)

        change_set = apply_trip_state_update(self.state, {"remove_places": ["City Palace"]})
        self.assertTrue(self.state.is_derived_stale(DerivedResource.CURRENT_ROUTE))

        action = ReplanningController.decide(
            state=self.state,
            user_message="remove City Palace",
            change_set=change_set,
        )
        self.assertEqual(action.action_type, ReplanningActionType.WAIT)
        self.assertEqual(action.reason, "lazy_replanning_mutation_without_explicit_replan_request")
        print("  ✓ Test 6 Passed: lazy mutation does not auto-rebuild -> WAIT")

    # =========================================================================
    # Test 7: explicit mutation + rebuild executes rebuild
    # =========================================================================
    def test_07_explicit_mutation_and_rebuild(self):
        """Mutating state with explicit rebuild request yields REBUILD_ROUTE immediately."""
        self.state.selected_places = [
            make_test_place("p1", "City Palace"),
            make_test_place("p2", "Jagdish Temple"),
        ]
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)

        change_set = apply_trip_state_update(self.state, {"remove_places": ["City Palace"]})
        action = ReplanningController.decide(
            state=self.state,
            user_message="remove City Palace and rebuild the route",
            change_set=change_set,
        )
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ROUTE)
        print("  ✓ Test 7 Passed: explicit mutation + rebuild -> REBUILD_ROUTE")

    # =========================================================================
    # Test 8: stale route + itinerary request rebuilds route first (chained)
    # =========================================================================
    def test_08_stale_route_chains_before_itinerary(self):
        """If route is STALE, requesting itinerary rebuilds route FIRST, chaining itinerary rebuild."""
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)
        self.state.current_itinerary = make_dummy_itinerary()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ITINERARY)

        action = ReplanningController.decide(
            state=self.state,
            user_intent="ITINERARY_REQUEST",
            user_message="update my itinerary",
        )
        # First action must be route rebuild!
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ROUTE)
        self.assertIsNotNone(action.chained_action)
        self.assertEqual(action.chained_action.action_type, ReplanningActionType.REBUILD_ITINERARY)
        print("  ✓ Test 8 Passed: stale route + itinerary request rebuilds route first with chained itinerary")

    # =========================================================================
    # Test 9: stale discovery + explicit discovery request
    # =========================================================================
    def test_09_stale_discovery_explicit_request(self):
        """Requesting places or food searches yields SEARCH_PLACES or SEARCH_FOOD."""
        action_places = ReplanningController.decide(
            state=self.state,
            user_message="show me places again",
        )
        self.assertEqual(action_places.action_type, ReplanningActionType.SEARCH_PLACES)

        action_food = ReplanningController.decide(
            state=self.state,
            user_message="find restaurants again",
        )
        self.assertEqual(action_food.action_type, ReplanningActionType.SEARCH_FOOD)
        print("  ✓ Test 9 Passed: stale discovery + explicit discovery request -> SEARCH_PLACES/FOOD")

    # =========================================================================
    # Test 10: successful rebuild -> VALID with updated provenance
    # =========================================================================
    @patch("app.agent.replanner.optimize_route_tool")
    def test_10_successful_rebuild_marks_valid(self, mock_opt):
        """Successful execution marks route VALID with active state_version."""
        dummy_route = make_dummy_route()
        mock_opt.return_value = {"success": True, "route": dummy_route.model_dump()}

        self.state.current_route = None
        self.state.mark_derived_not_available(DerivedResource.CURRENT_ROUTE)

        action = ReplanningAction(
            action_type=ReplanningActionType.REBUILD_ROUTE,
            reason="test_rebuild",
            tool_name="optimize_route",
            tool_args={},
        )
        res = execute_replanning_cycle(self.state, action)
        self.assertTrue(res["success"])
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)
        self.assertEqual(self.state.get_derived_version(DerivedResource.CURRENT_ROUTE), self.state.state_version)
        print("  ✓ Test 10 Passed: successful rebuild -> VALID with updated state_version provenance")

    # =========================================================================
    # Test 11: failed rebuild -> remains STALE
    # =========================================================================
    @patch("app.agent.replanner.optimize_route_tool")
    def test_11_failed_rebuild_remains_stale(self, mock_opt):
        """If route tool returns error, route remains STALE and failure is reported."""
        mock_opt.return_value = {"success": False, "error": "Routing API timed out"}

        self.state.current_route = make_dummy_route()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)

        action = ReplanningAction(
            action_type=ReplanningActionType.REBUILD_ROUTE,
            reason="test_rebuild_failure",
            tool_name="optimize_route",
            tool_args={},
        )
        res = execute_replanning_cycle(self.state, action)
        self.assertFalse(res["success"])
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertIn("Routing API timed out", res["error"])
        print("  ✓ Test 11 Passed: failed rebuild -> remains STALE with clean error report")

    # =========================================================================
    # Test 12: multiple mutations -> one coherent replan
    # =========================================================================
    def test_12_multiple_mutations_single_replan(self):
        """Applying multiple mutations coherently results in a single coherent route rebuild."""
        self.state.selected_places = [
            make_test_place("p1", "City Palace"),
            make_test_place("p2", "Jagdish Temple"),
        ]
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)

        change_set = apply_trip_state_update(self.state, {
            "travel_mode": "walking",
            "remove_places": ["City Palace"],
        })
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)

        action = ReplanningController.decide(
            state=self.state,
            user_intent="ROUTE_REQUEST",
            user_message="rebuild route",
            change_set=change_set,
        )
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ROUTE)
        self.assertEqual(action.tool_args.get("travel_mode"), "walking")
        print("  ✓ Test 12 Passed: multiple mutations -> single coherent replan with updated inputs")

    # =========================================================================
    # Test 13: old response rejected by state version
    # =========================================================================
    def test_13_old_response_rejected_by_state_version(self):
        """Stale response computed against older version is rejected and does not become VALID."""
        self.state.state_version = 15
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)

        # Attempt to mark valid with older state_version 14
        accepted = self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE, result_state_version=14)
        self.assertFalse(accepted)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)

        # Fresh response for active version 15
        accepted_fresh = self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE, result_state_version=15)
        self.assertTrue(accepted_fresh)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)
        print("  ✓ Test 13 Passed: old response rejected by state version provenance")

    # =========================================================================
    # Test 14: replanning loop terminates (finite execution)
    # =========================================================================
    def test_14_replanning_loop_terminates(self):
        """Chained replanning terminates strictly within max_steps (finite execution guarantee)."""
        action = ReplanningAction(
            action_type=ReplanningActionType.ANSWER,
            reason="terminal_action",
        )
        res = execute_replanning_cycle(self.state, action, max_steps=3)
        self.assertTrue(res["success"])
        self.assertLessEqual(res["steps_taken"], 1)
        print("  ✓ Test 14 Passed: replanning cycle terminates strictly (zero loop risk)")

    # =========================================================================
    # Test 15: unrelated user query does not trigger unrelated stale recomputation
    # =========================================================================
    def test_15_unrelated_query_preserves_stale_state(self):
        """Asking about weather or history does not rebuild a stale route."""
        self.state.current_route = make_dummy_route()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)

        action = ReplanningController.decide(
            state=self.state,
            user_message="what is the best time to visit Udaipur?",
        )
        self.assertEqual(action.action_type, ReplanningActionType.ANSWER)
        self.assertEqual(action.reason, "unrelated_query_preserves_stale_state")
        self.assertTrue(self.state.is_derived_stale(DerivedResource.CURRENT_ROUTE))
        print("  ✓ Test 15 Passed: unrelated query does not trigger unrelated stale recomputation")

    # =========================================================================
    # Test 16: stale data is never used as current planning input
    # =========================================================================
    def test_16_stale_route_never_used_in_itinerary(self):
        """Itinerary tool rejects if the underlying route is STALE."""
        from app.agent.tools import generate_itinerary_tool

        self.state.current_route = make_dummy_route()
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)
        self.assertTrue(self.state.is_derived_stale(DerivedResource.CURRENT_ROUTE))

        res = generate_itinerary_tool(trip_state=self.state)
        self.assertFalse(res["success"])
        self.assertIn("stale or not available", res["error"])
        print("  ✓ Test 16 Passed: stale route is strictly rejected from itinerary generation")

    # =========================================================================
    # Test 17: UI/user-facing serialization does not expose internal freshness
    # =========================================================================
    def test_17_sanitizer_removes_internal_freshness_tokens(self):
        """Public response sanitizer strips raw enum names and tokens."""
        raw_text = "CURRENT_ROUTE: STALE. I removed the stop. derived_freshness state_version: 5."
        cleaned = sanitize_public_response(raw_text)
        self.assertNotIn("CURRENT_ROUTE", cleaned)
        self.assertNotIn("STALE", cleaned)
        self.assertNotIn("derived_freshness", cleaned)
        self.assertNotIn("state_version", cleaned)
        self.assertIn("I removed the stop.", cleaned)
        print("  ✓ Test 17 Passed: public sanitizer cleans raw internal freshness & provenance tokens")

    # =========================================================================
    # Test 18: empty discovery result semantics are consistent
    # =========================================================================
    @patch("app.agent.tools.fetch_hotels_from_serpapi")
    def test_18_empty_discovery_semantics(self, mock_fetch):
        """Search execution with 0 results marks resource VALID with count 0 (not uncomputed)."""
        from app.agent.tools import search_hotels_tool

        mock_fetch.return_value = {"properties": []}
        res = search_hotels_tool(destination="Udaipur", trip_state=self.state)

        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 0)
        self.assertEqual(self.state.get_derived_status(DerivedResource.HOTEL_DISCOVERY), DerivedStateStatus.VALID)
        print("  ✓ Test 18 Passed: empty discovery results consistently marked VALID with 0 candidates")

    # =========================================================================
    # Test 19: all derived resources preserve correct state-version provenance
    # =========================================================================
    def test_19_all_resources_preserve_state_version_provenance(self):
        """Every canonical DerivedResource accurately records state_version upon becoming VALID."""
        self.state.state_version = 42
        for res in DerivedResource:
            self.state.mark_derived_valid(res)
            self.assertEqual(self.state.get_derived_version(res), 42)
            self.assertEqual(self.state.get_derived_status(res), DerivedStateStatus.VALID)
        print("  ✓ Test 19 Passed: all 5 derived resources record accurate state_version provenance")

    # =========================================================================
    # Test 20: freshness authority is not duplicated
    # =========================================================================
    def test_20_freshness_authority_not_duplicated(self):
        """FreshnessRegistry remains the sole authority for state transitions."""
        # Calling via state delegates directly to FreshnessRegistry
        self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)
        self.assertEqual(
            FreshnessRegistry.get_status(self.state.derived_freshness, DerivedResource.CURRENT_ROUTE),
            DerivedStateStatus.VALID,
        )
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)
        self.assertEqual(
            FreshnessRegistry.get_status(self.state.derived_freshness, DerivedResource.CURRENT_ROUTE),
            DerivedStateStatus.STALE,
        )
        print("  ✓ Test 20 Passed: FreshnessRegistry is the sole canonical transition authority")


if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("PROJECT MUSAFIR — MILESTONE 3 BATCH 1: REPLANNING CONTROLLER")
    print("=" * 65 + "\n")
    unittest.main(verbosity=1)
