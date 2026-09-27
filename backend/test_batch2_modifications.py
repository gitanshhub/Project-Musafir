"""
Project Musafir — Milestone 3 Batch 2: Dynamic Trip Modifications & Entity-Specific Replanning Test Suite
Covers:
1. Clarification & 5-stage Reference Resolution (exact name, selected entity, visible ordinal, explicit ID, contextual)
2. Ambiguous references generate structured ClarificationRequest without mutating TripState
3. Hotel swapping with route-origin re-anchoring & preservation of selected places/food
4. Incomplete hotel data (missing coordinates) is rejected by route prerequisites
5. Entity-specific replacement of place, restaurant, and cafe using atomic mutation batches
6. Duration shortening preserves all selected stops and surfaces scheduling conflicts without silent deletion
7. Duration extension propagates correctly into timetable without fabricating arbitrary activities
8. Explicit budget updates with scope (TOTAL_HOTEL, NIGHTLY_HOTEL, TOTAL_TRIP)
9. Budget decrease invalidates hotel if ceiling exceeded, while valid hotel is retained
10. Ambiguous budget requests trigger clarification without mutating TripState
11. Compound mutations (e.g. hotel change + duration, replacement + route rebuild) applied atomically
12. Stale route rejection by itinerary engine
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

from app.agent.state import TripState, PlanningStage, clear_all_states
from app.agent.dependencies import DerivedResource
from app.agent.freshness import DerivedStateStatus, FreshnessRegistry
from app.agent.state_update import apply_trip_state_update
from app.agent.mutation import (
    MutationType,
    MutationCommand,
    MutationBatch,
    BudgetScope,
    apply_mutation_command,
    apply_mutation_batch,
)
from app.agent.resolution import (
    resolve_entity_reference,
    resolve_budget_phrase,
    build_entity_replacement_batch,
    ResolutionPrecedence,
    ClarificationRequest,
)
from app.agent.replanner import (
    ReplanningController,
    ReplanningActionType,
    check_route_prerequisites,
    check_itinerary_prerequisites,
    execute_replanning_cycle,
)
from app.agent.context import get_session, save_session
from app.agent.tools import generate_itinerary_tool, optimize_route_tool
from app.schemas.hotel import Hotel
from app.schemas.place import Place
from app.schemas.restaurant import Restaurant
from app.schemas.route import OptimizedRoute, RouteStop, RouteSegment
from app.schemas.itinerary import ItineraryResponse


class TestBatch2DynamicModifications(unittest.TestCase):
    """Milestone 3 Batch 2 Focused Test Suite."""

    def setUp(self):
        clear_all_states()
        self.state = TripState(conversation_id="test_b2_conv")
        self.state.destination = "Kerala"
        self.state.number_of_days = 5
        self.state.number_of_nights = 4
        self.state.trip_start_date = date(2026, 9, 24)
        self.state.trip_end_date = date(2026, 9, 28)
        self.state.hotel_budget = 5000.0
        self.state.hotel_total_budget = 20000.0

        # Set selected hotel A
        self.hotel_a = Hotel(
            data_id="h_bolgatty",
            name="Grand Hyatt Kochi Bolgatty",
            latitude=9.9880,
            longitude=76.2625,
            price_per_night=4500.0,
            currency="INR",
        )
        self.state.hotel_selection = self.hotel_a

        # Set selected places
        self.place_1 = Place(
            data_id="p_fort_kochi",
            name="Fort Kochi",
            latitude=9.9650,
            longitude=76.2420,
            rating=4.6,
        )
        self.place_2 = Place(
            data_id="p_marine_drive",
            name="Marine Drive Kochi",
            latitude=9.9780,
            longitude=76.2750,
            rating=4.4,
        )
        self.state.add_place(self.place_1)
        self.state.add_place(self.place_2)

        # Set selected restaurant
        self.rest_1 = Restaurant(
            data_id="r_malabar",
            name="Malabar Cafe",
            latitude=9.9885,
            longitude=76.2630,
            rating=4.5,
            price_level="₹₹",
        )
        self.state.add_restaurant(self.rest_1)

        # Build initial valid route & itinerary
        self.init_route = {
            "origin": {"name": self.hotel_a.name, "latitude": self.hotel_a.latitude, "longitude": self.hotel_a.longitude},
            "destination": {"name": self.hotel_a.name, "latitude": self.hotel_a.latitude, "longitude": self.hotel_a.longitude},
            "ordered_stops": [
                {"id": "p_fort_kochi", "name": "Fort Kochi", "latitude": 9.9650, "longitude": 76.2420, "type": "attraction"},
                {"id": "r_malabar", "name": "Malabar Cafe", "latitude": 9.9885, "longitude": 76.2630, "type": "restaurant"},
                {"id": "p_marine_drive", "name": "Marine Drive Kochi", "latitude": 9.9780, "longitude": 76.2750, "type": "attraction"},
            ],
            "segments": [
                {"from_stop": self.hotel_a.name, "to_stop": "Fort Kochi", "distance_meters": 4500.0, "duration_seconds": 600},
                {"from_stop": "Fort Kochi", "to_stop": "Malabar Cafe", "distance_meters": 3000.0, "duration_seconds": 400},
                {"from_stop": "Malabar Cafe", "to_stop": "Marine Drive Kochi", "distance_meters": 3000.0, "duration_seconds": 400},
                {"from_stop": "Marine Drive Kochi", "to_stop": self.hotel_a.name, "distance_meters": 5000.0, "duration_seconds": 820},
            ],
            "total_distance_meters": 15500.0,
            "total_duration_seconds": 2220,
            "mode": "driving",
            "score": 95.0,
        }
        self.state.current_route = OptimizedRoute(**self.init_route)
        self.state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)
        generate_itinerary_tool(trip_state=self.state)

        # Context session with visible hotels & places
        from app.agent.context import SessionState
        self.session = SessionState(conversation_id="test_b2_conv", trip_state=self.state)
        save_session(self.session)
        self.session.conversation_context.set_visible_items("hotel", [
            {"data_id": "h_bolgatty", "name": "Grand Hyatt Kochi Bolgatty", "price_per_night": 4500.0},
            {"data_id": "h_brunton", "name": "Brunton Boatyard", "price_per_night": 6000.0},
            {"data_id": "h_taj_malabar", "name": "Taj Malabar Resort & Spa", "price_per_night": 5500.0},
            {"data_id": "h_taj_gateway", "name": "Taj Gateway Hotel", "price_per_night": 3500.0},
        ])
        self.session.conversation_context.set_visible_items("place", [
            {"data_id": "p_fort_kochi", "name": "Fort Kochi"},
            {"data_id": "p_marine_drive", "name": "Marine Drive Kochi"},
            {"data_id": "p_mattancherry", "name": "Mattancherry Palace"},
            {"data_id": "p_cherai", "name": "Cherai Beach"},
        ])

    # =========================================================================
    # Test Group 1: Reference Resolution & Clarification Contract
    # =========================================================================

    def test_01_exact_name_reference_resolution(self):
        """Precedence 1: Exact name matches unambiguously."""
        res = resolve_entity_reference("Fort Kochi", self.state, self.session.conversation_context)
        self.assertFalse(res.is_ambiguous)
        self.assertIsNotNone(res.resolved_entity)
        self.assertEqual(res.resolved_entity.name, "Fort Kochi")
        self.assertEqual(res.resolved_entity.precedence, ResolutionPrecedence.EXACT_NAME)

    def test_02_stable_selected_entity_resolution(self):
        """Precedence 2: Reference to a selected entity by distinctive substring."""
        res = resolve_entity_reference("Bolgatty", self.state, self.session.conversation_context)
        self.assertFalse(res.is_ambiguous)
        self.assertIsNotNone(res.resolved_entity)
        self.assertEqual(res.resolved_entity.name, "Grand Hyatt Kochi Bolgatty")
        self.assertEqual(res.resolved_entity.precedence, ResolutionPrecedence.SELECTED_ENTITY)

    def test_03_visible_ordinal_reference_resolution(self):
        """Precedence 3: Visible item ordinal reference e.g. 'the second hotel' or '3rd place'."""
        res = resolve_entity_reference("the second hotel", self.state, self.session.conversation_context, entity_type="hotel")
        self.assertFalse(res.is_ambiguous)
        self.assertIsNotNone(res.resolved_entity)
        self.assertEqual(res.resolved_entity.name, "Brunton Boatyard")
        self.assertEqual(res.resolved_entity.precedence, ResolutionPrecedence.VISIBLE_ORDINAL)

    def test_04_ambiguous_entity_reference_triggers_clarification_no_mutation(self):
        """Ambiguous query (e.g. 'Taj' matching 2 Taj hotels) must return clarification without mutating state."""
        initial_ver = self.state.state_version
        res = resolve_entity_reference("Taj", self.state, self.session.conversation_context, entity_type="hotel")
        self.assertTrue(res.is_ambiguous)
        self.assertIsNotNone(res.clarification)
        self.assertEqual(res.clarification.field, "hotel")
        self.assertIn("Taj Malabar Resort & Spa", res.clarification.options)
        self.assertIn("Taj Gateway Hotel", res.clarification.options)
        # Verify state was untouched
        self.assertEqual(self.state.state_version, initial_ver)
        self.assertEqual(self.state.hotel_selection.name, "Grand Hyatt Kochi Bolgatty")

    def test_05_contextual_reference_resolution(self):
        """Precedence 5: Contextual references like 'the hotel' or 'the restaurant'."""
        res_hotel = resolve_entity_reference("the hotel", self.state, self.session.conversation_context)
        self.assertFalse(res_hotel.is_ambiguous)
        self.assertEqual(res_hotel.resolved_entity.name, "Grand Hyatt Kochi Bolgatty")

        res_rest = resolve_entity_reference("the restaurant", self.state, self.session.conversation_context)
        self.assertFalse(res_rest.is_ambiguous)
        self.assertEqual(res_rest.resolved_entity.name, "Malabar Cafe")

    # =========================================================================
    # Test Group 2: Hotel Swapping & Route Re-anchoring
    # =========================================================================

    def test_06_hotel_swap_invalidates_downstream_and_preserves_selections(self):
        """Changing hotel updates hotel_selection, invalidates route & itinerary, preserves places & food."""
        new_hotel = Hotel(
            data_id="h_brunton",
            name="Brunton Boatyard",
            latitude=9.9680,
            longitude=76.2440,
            price_per_night=6000.0,
            currency="INR",
        )
        cmd = MutationCommand(
            mutation_type=MutationType.CHANGE_HOTEL,
            value=new_hotel,
        )
        change_set = apply_mutation_command(self.state, cmd)

        self.assertEqual(self.state.hotel_selection.name, "Brunton Boatyard")
        # Invariants: places and food are preserved!
        self.assertEqual(len(self.state.selected_places), 2)
        self.assertEqual(len(self.state.selected_restaurants), 1)
        self.assertEqual(self.state.destination, "Kerala")

        # Route & itinerary become STALE
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)

    def test_07_route_rebuild_uses_new_hotel_as_origin(self):
        """Rebuilding route after hotel swap uses the new hotel as origin & destination."""
        new_hotel = Hotel(
            data_id="h_brunton",
            name="Brunton Boatyard",
            latitude=9.9680,
            longitude=76.2440,
            price_per_night=6000.0,
            currency="INR",
        )
        self.state.hotel_selection = new_hotel
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)

        action = ReplanningController.decide(self.state, user_intent="ROUTE_REQUEST")
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ROUTE)
        self.assertEqual(action.tool_args["origin"]["name"], "Brunton Boatyard")
        self.assertEqual(action.tool_args["origin"]["latitude"], 9.9680)
        self.assertEqual(action.tool_args["destination"]["name"], "Brunton Boatyard")

    def test_08_incomplete_hotel_data_rejected_by_route_prerequisites(self):
        """Hotel without coordinates is rejected before building route."""
        incomplete_hotel = Hotel(
            data_id="h_inc",
            name="Incomplete Resort",
            latitude=None,
            longitude=None,
        )
        self.state.hotel_selection = incomplete_hotel
        met, missing, prompt = check_route_prerequisites(self.state)
        self.assertFalse(met)
        self.assertIn("hotel_coordinates", missing)
        self.assertIn("missing location coordinates", prompt.lower())

    # =========================================================================
    # Test Group 3: Entity-Specific Replacement
    # =========================================================================

    def test_09_replace_place_with_atomic_mutation_batch(self):
        """Replacing Fort Kochi with Mattancherry Palace applies atomic [REMOVE, SELECT] batch."""
        target_res = resolve_entity_reference("Fort Kochi", self.state, self.session.conversation_context)
        self.assertIsNotNone(target_res.resolved_entity)

        batch = build_entity_replacement_batch(
            target_res.resolved_entity,
            {"name": "Mattancherry Palace", "latitude": 9.9580, "longitude": 76.2590, "data_id": "p_mattancherry"},
        )
        self.assertEqual(len(batch.mutations), 2)
        self.assertEqual(batch.mutations[0].mutation_type, MutationType.REMOVE_PLACE)
        self.assertEqual(batch.mutations[1].mutation_type, MutationType.SELECT_PLACE)

        change_set = apply_mutation_batch(self.state, batch)
        place_names = [p.name for p in self.state.selected_places]
        self.assertNotIn("Fort Kochi", place_names)
        self.assertIn("Mattancherry Palace", place_names)
        self.assertIn("Marine Drive Kochi", place_names)  # other place preserved

        # Route & itinerary invalidated to STALE
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)

    def test_10_replace_restaurant_preserves_food_constraints(self):
        """Replacing Malabar Cafe removes it and selects Paragon Restaurant without corrupting places."""
        target_res = resolve_entity_reference("Malabar Cafe", self.state, self.session.conversation_context)
        batch = build_entity_replacement_batch(
            target_res.resolved_entity,
            {"name": "Paragon Restaurant", "latitude": 9.9910, "longitude": 76.2710, "data_id": "r_paragon"},
        )
        apply_mutation_batch(self.state, batch)

        rest_names = [r.name for r in self.state.selected_restaurants]
        self.assertNotIn("Malabar Cafe", rest_names)
        self.assertIn("Paragon Restaurant", rest_names)
        # Places completely intact
        self.assertEqual(len(self.state.selected_places), 2)

    # =========================================================================
    # Test Group 4: Duration / Date Propagation & Conflict Detection
    # =========================================================================

    def test_11_duration_shortening_preserves_stops_and_surfaces_conflict(self):
        """Shortening trip from 5 days to 1 day does NOT delete places; surfaces conflict in itinerary."""
        # Update duration to 1 day
        apply_trip_state_update(self.state, {"number_of_days": 1})
        self.assertEqual(self.state.number_of_days, 1)
        # Places and food must NOT be deleted!
        self.assertEqual(len(self.state.selected_places), 2)
        self.assertEqual(len(self.state.selected_restaurants), 1)

        # Generating itinerary with 3 stops across 1 day:
        # Mocking 2 days needed by route stops
        mock_multi_day_itin = {
            "days": [
                {
                    "day_number": 1,
                    "date": "2026-09-24",
                    "start_time": "09:00",
                    "end_time": "18:00",
                    "items": [{"stop_id": "1", "name": "Fort Kochi", "duration_minutes": 60, "arrival_time": "09:30", "departure_time": "10:30"}],
                },
                {
                    "day_number": 2,
                    "date": "2026-09-25",
                    "start_time": "09:00",
                    "end_time": "18:00",
                    "items": [{"stop_id": "2", "name": "Marine Drive Kochi", "duration_minutes": 60, "arrival_time": "09:30", "departure_time": "10:30"}],
                },
            ],
            "total_days": 2,
            "total_distance_meters": 15500.0,
            "total_travel_seconds": 2220.0,
            "total_visit_minutes": 120,
        }
        with patch("app.agent.tools.service_generate_itinerary", return_value=ItineraryResponse(**mock_multi_day_itin)):
            res = generate_itinerary_tool(trip_state=self.state)
            self.assertTrue(res["success"])
            self.assertTrue(res.get("conflict"))
            self.assertEqual(res.get("allocated_days"), 1)
            self.assertEqual(res.get("required_days"), 2)
            self.assertIn("Marine Drive Kochi", res.get("unscheduled_stops"))
            self.assertIn("comfortably", res.get("conflict_message"))

        # Verify places are still intact in TripState
        self.assertEqual(len(self.state.selected_places), 2)

    def test_12_duration_extension_propagates_cleanly(self):
        """Extending trip duration updates days and marks itinerary stale without creating dummy activities."""
        apply_trip_state_update(self.state, {"number_of_days": 7})
        self.assertEqual(self.state.number_of_days, 7)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        self.assertEqual(len(self.state.selected_places), 2)

    def test_13_date_change_invalidates_itinerary(self):
        """Moving start date shifts calendar dates and invalidates itinerary."""
        apply_trip_state_update(self.state, {"trip_start_date": date(2026, 10, 5)})
        self.assertEqual(self.state.trip_start_date, date(2026, 10, 5))
        self.assertEqual(self.state.trip_end_date, date(2026, 10, 9))
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)
        # Route remains VALID (stops and coordinates did not change)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)

    # =========================================================================
    # Test Group 5: Hotel Budget Updates & Re-filtering
    # =========================================================================

    def test_14_budget_increase_retains_valid_hotel(self):
        """Increasing hotel budget ceiling keeps currently selected hotel if its price fits."""
        # Hotel A is 4500/night. Increasing budget to 8000/night.
        cmd = MutationCommand(
            mutation_type=MutationType.SET_BUDGET,
            value=8000.0,
            scope=BudgetScope.NIGHTLY_HOTEL,
        )
        change_set = apply_mutation_command(self.state, cmd)
        self.assertEqual(self.state.hotel_budget, 8000.0)
        # Hotel selection is retained
        self.assertIsNotNone(self.state.hotel_selection)
        self.assertEqual(self.state.hotel_selection.name, "Grand Hyatt Kochi Bolgatty")
        # Route remains VALID
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)

    def test_15_budget_decrease_removes_expensive_hotel_and_invalidates_route(self):
        """Decreasing hotel budget below current hotel price clears hotel and marks route/itinerary stale."""
        # Hotel A is 4500/night. Decreasing budget to 2000/night.
        cmd = MutationCommand(
            mutation_type=MutationType.SET_BUDGET,
            value=2000.0,
            scope=BudgetScope.NIGHTLY_HOTEL,
        )
        change_set = apply_mutation_command(self.state, cmd)
        self.assertEqual(self.state.hotel_budget, 2000.0)
        # Hotel selection is removed!
        self.assertIsNone(self.state.hotel_selection)
        self.assertIn("selected_hotel", change_set.invalidated_fields)
        # Downstream route & itinerary become STALE
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)

    def test_16_ambiguous_budget_phrase_triggers_clarification(self):
        """Budget phrase without explicit scope or amount triggers clarification without mutating state."""
        initial_ver = self.state.state_version
        b_res = resolve_budget_phrase("my budget is 25000", self.state)
        self.assertTrue(b_res.is_ambiguous)
        self.assertIsNotNone(b_res.clarification)
        self.assertEqual(b_res.clarification.field, "budget")
        self.assertIn("nightly hotel stay", b_res.clarification.question.lower())
        # State version untouched
        self.assertEqual(self.state.state_version, initial_ver)

    # =========================================================================
    # Test Group 6: Compound Mutations & Safety
    # =========================================================================

    def test_17_compound_hotel_and_duration_mutation(self):
        """Compound turn changing both hotel and duration updates state atomically."""
        new_hotel = Hotel(
            data_id="h_brunton",
            name="Brunton Boatyard",
            latitude=9.9680,
            longitude=76.2440,
            price_per_night=6000.0,
        )
        batch = MutationBatch(mutations=[
            MutationCommand(mutation_type=MutationType.CHANGE_HOTEL, value=new_hotel),
            MutationCommand(mutation_type=MutationType.SET_DURATION, value=7),
        ])
        change_set = apply_mutation_batch(self.state, batch)
        self.assertEqual(self.state.hotel_selection.name, "Brunton Boatyard")
        self.assertEqual(self.state.number_of_days, 7)
        self.assertEqual(self.state.number_of_nights, 6)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)

    def test_18_stale_route_strictly_rejected_by_itinerary_tool(self):
        """Calling generate_itinerary_tool when CURRENT_ROUTE is STALE is strictly rejected."""
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)
        res = generate_itinerary_tool(trip_state=self.state)
        self.assertFalse(res["success"])
        self.assertIn("stale or not available", res["error"])

    def test_19_valid_route_not_recomputed_on_itinerary_update(self):
        """Updating itinerary when route is VALID does not recompute route."""
        self.state.mark_derived_stale(DerivedResource.CURRENT_ITINERARY)
        self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)

        action = ReplanningController.decide(self.state, user_intent="ITINERARY_REQUEST")
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ITINERARY)
        self.assertIsNone(action.chained_action)  # No need to chain route rebuild!

    def test_20_failed_route_rebuild_remains_stale(self):
        """If route rebuilding tool fails, route status remains STALE."""
        self.state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)
        with patch("app.agent.replanner.optimize_route_tool", return_value={"success": False, "error": "OSRM Timeout"}):
            action = ReplanningController.decide(self.state, user_intent="ROUTE_REQUEST")
            res = execute_replanning_cycle(self.state, action)
            self.assertFalse(res["success"])
            self.assertEqual(self.state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)


if __name__ == "__main__":
    unittest.main()
