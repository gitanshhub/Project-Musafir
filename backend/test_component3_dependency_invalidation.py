"""
Project Musafir — Milestone 3 Component 3: Dependency Graph + Invalidation Model
Focused test suite verifying:
1. Canonical DerivedResource definitions.
2. Canonical DEPENDENCY_GRAPH structure.
3. Pure deterministic invalidation calculation (get_invalidated_resources).
4. Cascading destination invalidation.
5. Hotel change invalidation semantics (hotel discovery preserved vs route/downstream invalidated).
6. Stop changes (places, food) invalidating route and itinerary without dropping discovery.
7. Travel mode invalidating route and itinerary only.
8. Duration and dates invalidating itinerary only (route preserved).
9. Preferences affecting only respective discovery categories.
10. Multi-field change deduplication.
11. Safe handling of empty, None, and unknown fields.
12. Integration with apply_trip_state_update() and TripChangeSet reporting.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import unittest
from datetime import date
from typing import Set

from app.agent.dependencies import (
    DerivedResource,
    DEPENDENCY_GRAPH,
    get_invalidated_resources,
    get_field_dependencies,
    is_resource_invalidated,
)
from app.agent.state import TripState, PlanningStage
from app.agent.state_update import apply_trip_state_update, TripChangeSet
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import OptimizedRoute, RouteStop, RouteSegment
from app.schemas.itinerary import ItineraryResponse


def make_dummy_route() -> OptimizedRoute:
    """Creates a minimal valid OptimizedRoute for testing invalidation."""
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
    """Creates a minimal valid ItineraryResponse for testing invalidation."""
    return ItineraryResponse(
        days=[],
        total_days=1,
        total_distance_meters=3500.0,
        total_travel_seconds=600.0,
        total_visit_minutes=60,
    )


class TestComponent3DependencyInvalidation(unittest.TestCase):
    """Component 3: Explicit Dependency Graph + Invalidation Model."""

    # =========================================================================
    # Test Group A — Canonical Resources & Graph Structure
    # =========================================================================
    def test_01_canonical_derived_resources_defined(self):
        """Verify the 5 canonical derived resources exist and are strings."""
        expected = {
            "hotel_discovery",
            "place_discovery",
            "food_discovery",
            "current_route",
            "current_itinerary",
        }
        actual = {r.value for r in DerivedResource}
        self.assertEqual(actual, expected)
        print("  ✓ Canonical DerivedResource enumeration matches application resources")

    def test_02_dependency_graph_core_mappings(self):
        """Verify key source fields map to expected derived resources in DEPENDENCY_GRAPH."""
        # Destination maps to all 5
        self.assertIn("destination", DEPENDENCY_GRAPH)
        self.assertEqual(len(DEPENDENCY_GRAPH["destination"]), 5)

        # Travel mode maps to route and itinerary
        self.assertIn("travel_mode", DEPENDENCY_GRAPH)
        self.assertEqual(
            DEPENDENCY_GRAPH["travel_mode"],
            frozenset({DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY})
        )

        # Selected places maps to route and itinerary
        self.assertEqual(
            DEPENDENCY_GRAPH["selected_places"],
            frozenset({DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY})
        )

        # Dates & duration map to itinerary
        self.assertIn(DerivedResource.CURRENT_ITINERARY, DEPENDENCY_GRAPH["number_of_days"])
        self.assertNotIn(DerivedResource.CURRENT_ROUTE, DEPENDENCY_GRAPH["number_of_days"])
        self.assertIn(DerivedResource.CURRENT_ITINERARY, DEPENDENCY_GRAPH["trip_start_date"])
        print("  ✓ Core dependency graph relationships declared explicitly")

    # =========================================================================
    # Test Group B — Precise Single-Field Invalidation
    # =========================================================================
    def test_03_selected_places_invalidation(self):
        """selected_places invalidates route + itinerary, preserves discovery."""
        res = get_invalidated_resources(["selected_places"])
        self.assertEqual(res, {DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY})
        self.assertNotIn(DerivedResource.HOTEL_DISCOVERY, res)
        self.assertNotIn(DerivedResource.PLACE_DISCOVERY, res)
        self.assertNotIn(DerivedResource.FOOD_DISCOVERY, res)
        print("  ✓ selected_places invalidates route + itinerary, preserves discovery")

    def test_04_selected_food_invalidation(self):
        """selected_restaurants and selected_cafes invalidate route + itinerary only."""
        res_r = get_invalidated_resources(["selected_restaurants"])
        res_c = get_invalidated_resources(["selected_cafes"])
        self.assertEqual(res_r, {DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY})
        self.assertEqual(res_c, {DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY})
        print("  ✓ Food stop selections invalidate route + itinerary only")

    def test_05_travel_mode_invalidation(self):
        """travel_mode invalidates route + itinerary, never hotel or place discovery."""
        res = get_invalidated_resources(["travel_mode"])
        self.assertEqual(res, {DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY})
        self.assertFalse(is_resource_invalidated(DerivedResource.HOTEL_DISCOVERY, ["travel_mode"]))
        self.assertFalse(is_resource_invalidated(DerivedResource.PLACE_DISCOVERY, ["travel_mode"]))
        print("  ✓ travel_mode invalidates route + itinerary, never hotel or place discovery")

    # =========================================================================
    # Test Group C — Destination Cascade
    # =========================================================================
    def test_06_destination_cascade(self):
        """destination change invalidates all 5 derived resources."""
        res = get_invalidated_resources(["destination"])
        self.assertEqual(len(res), 5)
        for r in DerivedResource:
            self.assertIn(r, res)
        print("  ✓ Destination change cascades to all 5 derived resources")

    # =========================================================================
    # Test Group D — Hotel Selection Semantics
    # =========================================================================
    def test_07_selected_hotel_preserves_hotel_discovery(self):
        """
        selected_hotel change invalidates anchored places/food discovery, route, and itinerary,
        but crucially PRESERVES hotel_discovery (the hotel search result set).
        """
        res = get_invalidated_resources(["selected_hotel"])
        self.assertIn(DerivedResource.PLACE_DISCOVERY, res)
        self.assertIn(DerivedResource.FOOD_DISCOVERY, res)
        self.assertIn(DerivedResource.CURRENT_ROUTE, res)
        self.assertIn(DerivedResource.CURRENT_ITINERARY, res)
        self.assertNotIn(DerivedResource.HOTEL_DISCOVERY, res)
        print("  ✓ selected_hotel preserves HOTEL_DISCOVERY while invalidating downstream anchored data")

    # =========================================================================
    # Test Group E — Dates & Duration Invalidation
    # =========================================================================
    def test_08_duration_and_dates_invalidation(self):
        """Duration and dates invalidate itinerary, but preserve route and discoveries."""
        for field in ["number_of_days", "trip_start_date", "trip_end_date"]:
            res = get_invalidated_resources([field])
            self.assertEqual(res, {DerivedResource.CURRENT_ITINERARY})
            self.assertNotIn(DerivedResource.CURRENT_ROUTE, res)
            self.assertNotIn(DerivedResource.HOTEL_DISCOVERY, res)
        print("  ✓ Dates and duration affect itinerary only, preserving route and discoveries")

    # =========================================================================
    # Test Group F — Preferences Invalidation Isolation
    # =========================================================================
    def test_09_preference_invalidation_isolation(self):
        """Preferences invalidate only their specific discovery domain, not route/itinerary."""
        # Interests affect places only
        res_i = get_invalidated_resources(["interests"])
        self.assertEqual(res_i, {DerivedResource.PLACE_DISCOVERY})

        # Dining preferences affect food only
        res_c = get_invalidated_resources(["cuisine_preferences"])
        self.assertEqual(res_c, {DerivedResource.FOOD_DISCOVERY})

        res_d = get_invalidated_resources(["dietary_preferences"])
        self.assertEqual(res_d, {DerivedResource.FOOD_DISCOVERY})

        # Hotel budget affects hotel discovery
        res_b = get_invalidated_resources(["hotel_budget"])
        self.assertEqual(res_b, {DerivedResource.HOTEL_DISCOVERY})
        print("  ✓ Preferences invalidate only their respective discovery domains")

    # =========================================================================
    # Test Group G — Multi-field & Deduplication
    # =========================================================================
    def test_10_multi_field_deduplication(self):
        """Combining multiple fields yields a clean, deduplicated set of derived resources."""
        fields = ["selected_places", "travel_mode", "remove_restaurants", "selected_cafes"]
        res = get_invalidated_resources(fields)
        self.assertEqual(res, {DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY})
        print("  ✓ Multi-field change deduplicates invalidations cleanly")

    def test_11_unknown_or_empty_fields_safe(self):
        """Empty lists, None, and unknown fields produce empty invalidation sets safely."""
        self.assertEqual(get_invalidated_resources([]), set())
        self.assertEqual(get_invalidated_resources([None, ""]), set())
        self.assertEqual(get_invalidated_resources(["unknown_custom_field", "trip_budget"]), set())
        print("  ✓ Unknown, empty, and non-dependent fields produce empty invalidation sets safely")

    # =========================================================================
    # Test Group H — Helper Function Verification
    # =========================================================================
    def test_12_helper_functions(self):
        """Verify get_field_dependencies and is_resource_invalidated convenience helpers."""
        self.assertEqual(
            get_field_dependencies("travel_mode"),
            {DerivedResource.CURRENT_ROUTE, DerivedResource.CURRENT_ITINERARY}
        )
        self.assertEqual(get_field_dependencies("non_existent_field"), set())

        self.assertTrue(is_resource_invalidated(DerivedResource.CURRENT_ROUTE, ["travel_mode"]))
        self.assertFalse(is_resource_invalidated(DerivedResource.HOTEL_DISCOVERY, ["travel_mode"]))
        print("  ✓ Helper functions get_field_dependencies and is_resource_invalidated verified")

    # =========================================================================
    # Test Group I — Integration with apply_trip_state_update()
    # =========================================================================
    def test_13_state_update_place_removal_invalidates_route_and_itinerary(self):
        """
        Integration: When state has route + itinerary, removing a place
        uses the dependency engine to clear route and itinerary, and reports both in TripChangeSet.
        """
        state = TripState(destination="Jaipur")
        state.selected_places = [
            Place(data_id="p1", name="Hawa Mahal", latitude=26.9239, longitude=75.8267),
            Place(data_id="p2", name="Amber Palace", latitude=26.9855, longitude=75.8513),
        ]
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()
        self.assertIsNotNone(state.current_route)
        self.assertIsNotNone(state.current_itinerary)

        change_set = apply_trip_state_update(state, {"remove_places": ["Amber Palace"]})

        self.assertIn("selected_places", change_set.changed_fields)
        self.assertIn("current_route", change_set.invalidated_fields)
        self.assertIn("current_itinerary", change_set.invalidated_fields)
        self.assertIsNone(state.current_route)
        self.assertIsNone(state.current_itinerary)
        self.assertEqual(len(state.selected_places), 1)
        self.assertEqual(state.selected_places[0].name, "Hawa Mahal")
        print("  ✓ apply_trip_state_update uses dependency engine to invalidate route & itinerary on place removal")

    def test_14_state_update_travel_mode_preserves_hotels_and_stops(self):
        """
        Integration: Changing travel mode clears route and itinerary via dependency engine,
        while strictly preserving hotel_selection and selected_places.
        """
        state = TripState(destination="Jaipur", travel_mode="driving")
        state.hotel_selection = Hotel(name="ITC Rajputana", latitude=26.918, longitude=75.790, price_per_night=4500.0)
        state.selected_places = [Place(data_id="p1", name="Hawa Mahal", latitude=26.92, longitude=75.82)]
        state.selected_restaurants = [Restaurant(data_id="r1", name="Peacock Rooftop", latitude=26.91, longitude=75.79)]
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()

        change_set = apply_trip_state_update(state, {"travel_mode": "walking"})

        self.assertIn("travel_mode", change_set.changed_fields)
        self.assertIn("current_route", change_set.invalidated_fields)
        self.assertIn("current_itinerary", change_set.invalidated_fields)
        self.assertNotIn("selected_hotel", change_set.invalidated_fields)
        self.assertNotIn("selected_places", change_set.invalidated_fields)
        self.assertNotIn("selected_restaurants", change_set.invalidated_fields)

        # Verify preserved entities
        self.assertIsNotNone(state.hotel_selection)
        self.assertEqual(len(state.selected_places), 1)
        self.assertEqual(len(state.selected_restaurants), 1)
        # Verify invalidated derived outputs
        self.assertIsNone(state.current_route)
        self.assertIsNone(state.current_itinerary)
        print("  ✓ Changing travel mode clears route/itinerary while preserving hotel and stops")

    def test_15_state_update_duration_preserves_route(self):
        """
        Integration: Changing number_of_days invalidates itinerary, but PRESERVES route.
        """
        state = TripState(destination="Jaipur", number_of_days=3)
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()

        change_set = apply_trip_state_update(state, {"number_of_days": 5})

        self.assertIn("number_of_days", change_set.changed_fields)
        self.assertIn("current_itinerary", change_set.invalidated_fields)
        self.assertNotIn("current_route", change_set.invalidated_fields)
        self.assertIsNotNone(state.current_route, "Route geometry must be preserved on duration change")
        self.assertIsNone(state.current_itinerary, "Day timetable must be invalidated on duration change")
        print("  ✓ Changing duration invalidates itinerary timetable while preserving route geometry")

    def test_16_state_update_destination_cascades_completely(self):
        """
        Integration: Changing destination invalidates selections and derived route/itinerary.
        """
        state = TripState(destination="Jaipur")
        state.hotel_selection = Hotel(name="ITC Rajputana", latitude=26.918, longitude=75.790)
        state.selected_places = [Place(data_id="p1", name="Hawa Mahal", latitude=26.92, longitude=75.82)]
        state.selected_restaurants = [Restaurant(data_id="r1", name="Peacock", latitude=26.91, longitude=75.79)]
        state.selected_cafes = [Restaurant(data_id="c1", name="Curious Life", latitude=26.90, longitude=75.80)]
        state.current_route = make_dummy_route()
        state.current_itinerary = make_dummy_itinerary()

        change_set = apply_trip_state_update(state, {"destination": "Udaipur"})

        self.assertIn("destination", change_set.changed_fields)
        self.assertIn("selected_hotel", change_set.invalidated_fields)
        self.assertIn("selected_places", change_set.invalidated_fields)
        self.assertIn("selected_restaurants", change_set.invalidated_fields)
        self.assertIn("selected_cafes", change_set.invalidated_fields)
        self.assertIn("current_route", change_set.invalidated_fields)
        self.assertIn("current_itinerary", change_set.invalidated_fields)

        self.assertIsNone(state.hotel_selection)
        self.assertEqual(state.selected_places, [])
        self.assertEqual(state.selected_restaurants, [])
        self.assertEqual(state.selected_cafes, [])
        self.assertIsNone(state.current_route)
        self.assertIsNone(state.current_itinerary)
        print("  ✓ Destination change cleanly cascades across all selections and derived outputs")

    def test_17_no_changes_no_invalidations_no_version_bump(self):
        """
        Integration: Passing unchanged or empty updates produces no changed or invalidated fields,
        and does not bump state_version.
        """
        state = TripState(destination="Jaipur", state_version=5)
        state.current_route = make_dummy_route()
        v_before = state.state_version

        change_set = apply_trip_state_update(state, {"destination": "Jaipur"})
        self.assertFalse(change_set.has_changes())
        self.assertEqual(change_set.changed_fields, [])
        self.assertEqual(change_set.invalidated_fields, [])
        self.assertEqual(state.state_version, v_before)
        self.assertIsNotNone(state.current_route)
        print("  ✓ No-op updates perform zero invalidations and preserve state_version")


if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("PROJECT MUSAFIR — MILESTONE 3 COMPONENT 3: DEPENDENCY INVALIDATION")
    print("=" * 65 + "\n")
    unittest.main(verbosity=1)
