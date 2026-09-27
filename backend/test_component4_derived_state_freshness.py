"""
Project Musafir — Milestone 3 Component 4: Derived State Freshness Test Suite
Verifies the freshness lifecycle (VALID, STALE, NOT_AVAILABLE) and provenance tracking:
1. Initial absence of derived resources = NOT_AVAILABLE.
2. Successful hotel discovery = VALID.
3. Successful place discovery = VALID.
4. Successful food discovery = VALID.
5. Successful route computation = VALID.
6. Successful itinerary computation = VALID.
7. selected_places change transitions route + itinerary to STALE (preserves discoveries).
8. Food selection change transitions route + itinerary to STALE (preserves food discovery).
9. travel_mode change transitions route + itinerary to STALE (preserves all discoveries).
10. Duration & date changes transition itinerary to STALE (preserves route).
11. Destination cascade transitions all existing derived resources to STALE.
12. Absent results remain NOT_AVAILABLE upon invalidation (never falsely converted to STALE).
13. Successful recomputation transitions STALE -> VALID.
14. No-op mutations do NOT create false staleness.
15. Multiple invalidations deduplicate without error.
16. Freshness lifecycle remains fully synchronized with TripChangeSet.
17. State version provenance protects against obsolete late results becoming VALID.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import unittest
from typing import Set

from app.agent.dependencies import DerivedResource
from app.agent.freshness import (
    DerivedStateStatus,
    DerivedResourceFreshness,
    FreshnessRegistry,
    init_derived_freshness,
)
from app.agent.state import TripState, PlanningStage
from app.agent.state_update import apply_trip_state_update, TripChangeSet
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import RouteStop, RouteSegment, OptimizedRoute
from app.schemas.itinerary import ItineraryResponse


def make_dummy_route() -> OptimizedRoute:
    """Creates a minimal valid OptimizedRoute for testing."""
    stop1 = RouteStop(id="s1", name="Hotel Start", latitude=26.9124, longitude=75.7873, type="hotel")
    stop2 = RouteStop(id="s2", name="Hawa Mahal", latitude=26.9239, longitude=75.8267, type="attraction")
    seg = RouteSegment(from_stop="s1", to_stop="s2", distance_meters=3500.0, duration_seconds=600)
    return OptimizedRoute(
        ordered_stops=[stop1, stop2],
        segments=[seg],
        total_distance_meters=3500.0,
        total_duration_seconds=600,
        score=0.95,
    )


def make_dummy_itinerary() -> ItineraryResponse:
    """Creates a minimal valid ItineraryResponse for testing."""
    return ItineraryResponse(
        days=[],
        total_days=1,
        total_distance_meters=3500.0,
        total_travel_seconds=600.0,
        total_visit_minutes=60,
    )


class TestComponent4DerivedStateFreshness(unittest.TestCase):
    """Component 4: Derived State Freshness Lifecycle & Provenance Suite."""

    # =========================================================================
    # Test 1: Initial State = NOT_AVAILABLE
    # =========================================================================
    def test_01_initial_derived_resources_not_available(self):
        """Newly created TripState has all 5 derived resources in NOT_AVAILABLE status."""
        state = TripState(destination="Jaipur")
        for res in DerivedResource:
            status = state.get_derived_status(res)
            self.assertEqual(status, DerivedStateStatus.NOT_AVAILABLE)
            self.assertFalse(state.is_derived_valid(res))
            self.assertFalse(state.is_derived_stale(res))
            self.assertFalse(state.is_derived_available(res))
            self.assertIsNone(state.get_derived_version(res))
        print("  ✓ Test 1 Passed: Initial derived resources default to NOT_AVAILABLE with null provenance")

    # =========================================================================
    # Tests 2–6: Successful Computations = VALID
    # =========================================================================
    def test_02_successful_hotel_discovery_valid(self):
        """Successful hotel discovery marks HOTEL_DISCOVERY as VALID with state_version."""
        state = TripState(destination="Jaipur", state_version=3)
        marked = state.mark_derived_valid(DerivedResource.HOTEL_DISCOVERY)
        self.assertTrue(marked)
        self.assertEqual(state.get_derived_status(DerivedResource.HOTEL_DISCOVERY), DerivedStateStatus.VALID)
        self.assertTrue(state.is_derived_valid(DerivedResource.HOTEL_DISCOVERY))
        self.assertEqual(state.get_derived_version(DerivedResource.HOTEL_DISCOVERY), 3)
        print("  ✓ Test 2 Passed: Successful hotel discovery marks HOTEL_DISCOVERY as VALID")

    def test_03_successful_place_discovery_valid(self):
        """Successful place discovery marks PLACE_DISCOVERY as VALID."""
        state = TripState(destination="Jaipur", state_version=4)
        marked = state.mark_derived_valid(DerivedResource.PLACE_DISCOVERY)
        self.assertTrue(marked)
        self.assertEqual(state.get_derived_status(DerivedResource.PLACE_DISCOVERY), DerivedStateStatus.VALID)
        self.assertTrue(state.is_derived_valid(DerivedResource.PLACE_DISCOVERY))
        self.assertEqual(state.get_derived_version(DerivedResource.PLACE_DISCOVERY), 4)
        print("  ✓ Test 3 Passed: Successful place discovery marks PLACE_DISCOVERY as VALID")

    def test_04_successful_food_discovery_valid(self):
        """Successful food discovery marks FOOD_DISCOVERY as VALID."""
        state = TripState(destination="Jaipur", state_version=5)
        marked = state.mark_derived_valid(DerivedResource.FOOD_DISCOVERY)
        self.assertTrue(marked)
        self.assertEqual(state.get_derived_status(DerivedResource.FOOD_DISCOVERY), DerivedStateStatus.VALID)
        self.assertTrue(state.is_derived_valid(DerivedResource.FOOD_DISCOVERY))
        self.assertEqual(state.get_derived_version(DerivedResource.FOOD_DISCOVERY), 5)
        print("  ✓ Test 4 Passed: Successful food discovery marks FOOD_DISCOVERY as VALID")

    def test_05_successful_route_valid(self):
        """Setting or marking route valid transitions CURRENT_ROUTE to VALID."""
        state = TripState(destination="Jaipur", current_route=make_dummy_route(), state_version=6)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ROUTE))
        self.assertEqual(state.get_derived_version(DerivedResource.CURRENT_ROUTE), 6)
        print("  ✓ Test 5 Passed: Successful route generation marks CURRENT_ROUTE as VALID")

    def test_06_successful_itinerary_valid(self):
        """Setting or marking itinerary valid transitions CURRENT_ITINERARY to VALID."""
        state = TripState(destination="Jaipur", current_itinerary=make_dummy_itinerary(), state_version=7)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.VALID)
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ITINERARY))
        self.assertEqual(state.get_derived_version(DerivedResource.CURRENT_ITINERARY), 7)
        print("  ✓ Test 6 Passed: Successful itinerary generation marks CURRENT_ITINERARY as VALID")

    # =========================================================================
    # Tests 7–10: Invalidation Transitions (VALID -> STALE)
    # =========================================================================
    def test_07_selected_places_invalidates_route_and_itinerary_to_stale(self):
        """
        When route and itinerary are VALID, modifying selected_places
        transitions CURRENT_ROUTE and CURRENT_ITINERARY to STALE while preserving discoveries.
        """
        state = TripState(destination="Jaipur")
        state.selected_places = [Place(data_id="p1", name="Hawa Mahal", latitude=26.9, longitude=75.8)]
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()
        state.mark_derived_valid(DerivedResource.HOTEL_DISCOVERY)
        state.mark_derived_valid(DerivedResource.PLACE_DISCOVERY)
        state.mark_derived_valid(DerivedResource.FOOD_DISCOVERY)

        # Mutate selected places
        change_set = apply_trip_state_update(state, {"remove_places": ["Hawa Mahal"]})

        # Route and itinerary are now STALE
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        self.assertTrue(state.is_derived_stale(DerivedResource.CURRENT_ROUTE))
        self.assertTrue(state.is_derived_stale(DerivedResource.CURRENT_ITINERARY))

        # Discoveries remain strictly VALID
        self.assertEqual(state.get_derived_status(DerivedResource.HOTEL_DISCOVERY), DerivedStateStatus.VALID)
        self.assertEqual(state.get_derived_status(DerivedResource.PLACE_DISCOVERY), DerivedStateStatus.VALID)
        self.assertEqual(state.get_derived_status(DerivedResource.FOOD_DISCOVERY), DerivedStateStatus.VALID)
        print("  ✓ Test 7 Passed: selected_places modification marks route/itinerary STALE while preserving discoveries")

    def test_08_food_selection_invalidates_route_and_itinerary_to_stale(self):
        """Adding a restaurant transitions route and itinerary to STALE, preserving FOOD_DISCOVERY."""
        state = TripState(destination="Jaipur")
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()
        state.mark_derived_valid(DerivedResource.FOOD_DISCOVERY)

        change_set = apply_trip_state_update(state, {
            "selected_restaurants": [{"data_id": "r1", "name": "Peacock Rooftop", "latitude": 26.91, "longitude": 75.79}]
        })

        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.FOOD_DISCOVERY), DerivedStateStatus.VALID)
        print("  ✓ Test 8 Passed: Food stop selection marks route/itinerary STALE, preserves FOOD_DISCOVERY")

    def test_09_travel_mode_invalidates_route_and_itinerary_to_stale(self):
        """Changing travel mode transitions route and itinerary to STALE, preserving all discoveries."""
        state = TripState(destination="Jaipur", travel_mode="driving")
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()
        state.mark_derived_valid(DerivedResource.HOTEL_DISCOVERY)
        state.mark_derived_valid(DerivedResource.PLACE_DISCOVERY)
        state.mark_derived_valid(DerivedResource.FOOD_DISCOVERY)

        change_set = apply_trip_state_update(state, {"travel_mode": "walking"})

        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.HOTEL_DISCOVERY), DerivedStateStatus.VALID)
        self.assertEqual(state.get_derived_status(DerivedResource.PLACE_DISCOVERY), DerivedStateStatus.VALID)
        self.assertEqual(state.get_derived_status(DerivedResource.FOOD_DISCOVERY), DerivedStateStatus.VALID)
        print("  ✓ Test 9 Passed: travel_mode marks route/itinerary STALE, preserves all discoveries")

    def test_10_duration_and_date_changes_invalidate_itinerary_only(self):
        """Duration change transitions CURRENT_ITINERARY to STALE, while CURRENT_ROUTE remains VALID."""
        state = TripState(destination="Jaipur", number_of_days=3)
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()

        change_set = apply_trip_state_update(state, {"number_of_days": 5})

        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ROUTE))
        print("  ✓ Test 10 Passed: Duration change marks itinerary STALE while keeping route VALID")

    # =========================================================================
    # Test 11: Destination Cascade
    # =========================================================================
    def test_11_destination_cascade_marks_all_existing_results_stale(self):
        """Destination change marks all currently existing/valid derived resources STALE."""
        state = TripState(destination="Jaipur")
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()
        state.mark_derived_valid(DerivedResource.HOTEL_DISCOVERY)
        state.mark_derived_valid(DerivedResource.PLACE_DISCOVERY)
        state.mark_derived_valid(DerivedResource.FOOD_DISCOVERY)

        change_set = apply_trip_state_update(state, {"destination": "Udaipur"})

        for res in DerivedResource:
            self.assertEqual(
                state.get_derived_status(res),
                DerivedStateStatus.STALE,
                f"Resource {res.value} must be STALE after destination change"
            )
        print("  ✓ Test 11 Passed: Destination change cascades to mark all existing derived resources STALE")

    # =========================================================================
    # Test 12: Absent Results Remain NOT_AVAILABLE
    # =========================================================================
    def test_12_absent_results_remain_not_available(self):
        """
        If a resource never existed (NOT_AVAILABLE), dependency invalidation
        does NOT turn it into STALE. It remains strictly NOT_AVAILABLE.
        """
        state = TripState(destination="Jaipur")
        # No route, no itinerary, no hotel discovery
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.NOT_AVAILABLE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.NOT_AVAILABLE)

        # Mutate selected places
        change_set = apply_trip_state_update(state, {
            "selected_places": [{"data_id": "p1", "name": "Amber Fort", "latitude": 26.98, "longitude": 75.85}]
        })

        # Must remain NOT_AVAILABLE, NOT STALE
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.NOT_AVAILABLE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.NOT_AVAILABLE)
        self.assertFalse(state.is_derived_stale(DerivedResource.CURRENT_ROUTE))
        self.assertNotIn("current_route", change_set.invalidated_fields)
        print("  ✓ Test 12 Passed: Non-existent results remain NOT_AVAILABLE upon invalidation (never false STALE)")

    # =========================================================================
    # Test 13: STALE -> VALID Recomputation
    # =========================================================================
    def test_13_recomputation_transitions_stale_to_valid(self):
        """Recomputing a STALE resource cleanly transitions it back to VALID with updated provenance."""
        state = TripState(destination="Jaipur")
        state.current_route = make_dummy_route()
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ROUTE))

        # Invalidate route
        apply_trip_state_update(state, {"travel_mode": "walking"})
        self.assertTrue(state.is_derived_stale(DerivedResource.CURRENT_ROUTE))
        self.assertIsNone(state.current_route)

        # Recompute route
        new_route = make_dummy_route()
        state.current_route = new_route
        state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)

        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ROUTE))
        self.assertFalse(state.is_derived_stale(DerivedResource.CURRENT_ROUTE))
        self.assertEqual(state.get_derived_version(DerivedResource.CURRENT_ROUTE), state.state_version)
        print("  ✓ Test 13 Passed: Successful recomputation transitions STALE back to VALID with current state_version")

    # =========================================================================
    # Test 14: No-op Mutations Do Not Create False Staleness
    # =========================================================================
    def test_14_no_op_mutation_preserves_freshness(self):
        """Mutating a field to its identical existing value does not alter freshness."""
        state = TripState(destination="Jaipur", travel_mode="driving")
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ROUTE))
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ITINERARY))

        # Apply identical travel mode
        change_set = apply_trip_state_update(state, {"travel_mode": "driving"})
        self.assertFalse(change_set.has_changes())

        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.VALID)
        self.assertIsNotNone(state.current_route)
        self.assertIsNotNone(state.current_itinerary)
        print("  ✓ Test 14 Passed: No-op mutations preserve existing VALID freshness without false staleness")

    # =========================================================================
    # Test 15: Multiple Invalidations Deduplicate
    # =========================================================================
    def test_15_multiple_invalidations_deduplicate(self):
        """Multiple triggers affecting the same derived resource do not produce duplicate or contradictory states."""
        state = TripState(destination="Jaipur", travel_mode="driving")
        state.selected_places = [Place(data_id="p1", name="Hawa Mahal", latitude=26.9, longitude=75.8)]
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()

        # Both travel_mode and remove_places invalidate route & itinerary
        change_set = apply_trip_state_update(state, {
            "travel_mode": "walking",
            "remove_places": ["Hawa Mahal"],
        })

        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        self.assertEqual(change_set.invalidated_fields.count("current_route"), 1)
        self.assertEqual(change_set.invalidated_fields.count("current_itinerary"), 1)
        print("  ✓ Test 15 Passed: Multi-trigger invalidations deduplicate cleanly without contradictory states")

    # =========================================================================
    # Test 16: Consistency with TripChangeSet
    # =========================================================================
    def test_16_freshness_consistent_with_trip_change_set(self):
        """TripChangeSet and derived_freshness accurately align on changed and invalidated fields."""
        state = TripState(destination="Jaipur")
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()

        change_set = apply_trip_state_update(state, {"number_of_days": 4})

        # TripChangeSet reported fields
        self.assertIn("number_of_days", change_set.changed_fields)
        self.assertIn("current_itinerary", change_set.invalidated_fields)
        self.assertNotIn("current_route", change_set.invalidated_fields)

        # Freshness statuses
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)

        # Summary dictionary contains derived_freshness map
        summary = state.summary()
        self.assertIn("derived_freshness", summary)
        self.assertEqual(summary["derived_freshness"]["current_itinerary"], "stale")
        self.assertEqual(summary["derived_freshness"]["current_route"], "valid")
        print("  ✓ Test 16 Passed: TripChangeSet and derived_freshness remain 100% consistent")

    # =========================================================================
    # Test 17: Provenance / Stale Response Protection
    # =========================================================================
    def test_17_stale_response_protection(self):
        """
        A delayed tool result generated against an older state_version
        is rejected and does NOT become VALID for the updated state.
        """
        state = TripState(destination="Jaipur", state_version=10)
        state.current_route = make_dummy_route()
        state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)

        # State updates: version increments to 11, route becomes STALE
        apply_trip_state_update(state, {"travel_mode": "walking"})
        self.assertEqual(state.state_version, 11)
        self.assertTrue(state.is_derived_stale(DerivedResource.CURRENT_ROUTE))

        # A late response from old version 10 arrives
        accepted = state.mark_derived_valid(DerivedResource.CURRENT_ROUTE, result_state_version=10)
        self.assertFalse(accepted, "Obsolete late response (v10 < v11) must be rejected")
        self.assertTrue(state.is_derived_stale(DerivedResource.CURRENT_ROUTE), "Status must remain STALE")

        # A fresh response for current version 11 arrives
        accepted_fresh = state.mark_derived_valid(DerivedResource.CURRENT_ROUTE, result_state_version=11)
        self.assertTrue(accepted_fresh, "Current response (v11 == v11) must be accepted")
        self.assertTrue(state.is_derived_valid(DerivedResource.CURRENT_ROUTE), "Status must transition to VALID")
        self.assertEqual(state.get_derived_version(DerivedResource.CURRENT_ROUTE), 11)
        print("  ✓ Test 17 Passed: Provenance protects against delayed stale results becoming VALID")


if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("PROJECT MUSAFIR — MILESTONE 3 COMPONENT 4: DERIVED STATE FRESHNESS")
    print("=" * 65 + "\n")
    unittest.main(verbosity=1)
