import unittest
from datetime import date
from typing import Dict, Any

from app.schemas.mutation import BudgetScope
from app.schemas.hotel import HotelDetail
from app.agent.state import TripState, Place, DerivedResource, DerivedStateStatus
from app.agent.state_update import apply_trip_state_update
from app.agent.resolution import (
    resolve_entity_reference,
    resolve_budget_phrase,
    build_entity_replacement_batch,
    ResolutionPrecedence,
)
from app.agent.context import ConversationContext, SessionState
from app.agent.fast_path import resolve_fast_path
from app.agent.replanner import ReplanningController, ReplanningActionType


class TestBatch21Hardening(unittest.TestCase):
    """
    Milestone 3 Batch 2.1 — Edge-Case Hardening & Invariant Verification Suite.
    """

    # =========================================================================
    # A. BUDGET HARDENING
    # =========================================================================

    def test_valid_budget_increase_preserves_hotel(self):
        """Budget increase where hotel is under ceiling preserves hotel and its validity."""
        state = TripState(
            destination="Kochi",
            hotel_selection=HotelDetail(
                property_token="h_hyatt",
                name="Grand Hyatt Kochi Bolgatty",
                price_per_night=5000.0,
                latitude=9.9880,
                longitude=76.2625,
            ),
            hotel_budget=5500.0,
        )
        state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)
        state.mark_derived_valid(DerivedResource.CURRENT_ITINERARY)

        cs = apply_trip_state_update(state, {"hotel_budget": 8000.0})
        self.assertIn("hotel_budget", cs.changed_fields)
        self.assertNotIn("selected_hotel", cs.invalidated_fields)
        self.assertIsNotNone(state.hotel_selection)
        self.assertEqual(state.hotel_selection.name, "Grand Hyatt Kochi Bolgatty")
        # Route remains valid because hotel did not change
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.VALID)

    def test_budget_decrease_invalidates_hotel_exceeding_ceiling(self):
        """Budget decrease below hotel price invalidates hotel and cascades route/itinerary to STALE."""
        state = TripState(
            destination="Kochi",
            hotel_selection=HotelDetail(
                property_token="h_hyatt",
                name="Grand Hyatt Kochi Bolgatty",
                price_per_night=6000.0,
                latitude=9.9880,
                longitude=76.2625,
            ),
            hotel_budget=7000.0,
        )
        state.mark_derived_valid(DerivedResource.CURRENT_ROUTE)
        state.mark_derived_valid(DerivedResource.CURRENT_ITINERARY)

        cs = apply_trip_state_update(state, {"hotel_budget": 4000.0})
        self.assertIn("hotel_budget", cs.changed_fields)
        self.assertIn("selected_hotel", cs.invalidated_fields)
        self.assertIsNone(state.hotel_selection)
        # Cascades to STALE
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ROUTE), DerivedStateStatus.STALE)
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)

    def test_ambiguous_budget_requests_clarification_without_mutation(self):
        """Budget with no inferable scope returns clarification with zero state mutations."""
        state = TripState(destination="Kochi", hotel_budget=5000.0)
        res = resolve_budget_phrase("budget is 30000", state)
        self.assertTrue(res.is_ambiguous)
        self.assertIsNotNone(res.clarification)
        self.assertEqual(res.clarification.field, "budget")
        self.assertEqual(state.hotel_budget, 5000.0)  # Zero state mutation!

    def test_total_hotel_and_nightly_never_silently_interchanged(self):
        """Explicit scopes maintain strict separation."""
        state = TripState(destination="Kochi")
        res_nightly = resolve_budget_phrase("₹4,000 per night", state)
        self.assertEqual(res_nightly.scope, BudgetScope.NIGHTLY_HOTEL)
        self.assertEqual(res_nightly.amount, 4000.0)

        res_total = resolve_budget_phrase("hotel budget 20000 for total hotel", state)
        self.assertEqual(res_total.scope, BudgetScope.TOTAL_HOTEL)
        self.assertEqual(res_total.amount, 20000.0)

        res_trip = resolve_budget_phrase("I can spend 50000 for the whole trip", state)
        self.assertEqual(res_trip.scope, BudgetScope.TOTAL_TRIP)
        self.assertEqual(res_trip.amount, 50000.0)

    # =========================================================================
    # B. CLARIFICATION MEMORY & MULTI-TURN RESOLUTION
    # =========================================================================

    def test_clarification_memory_hotel_ordinal_answer(self):
        """Turn 1 asks clarification -> Turn 2 'second one' resolves against options to select hotel."""
        session = SessionState(
            conversation_id="clar_test_1",
            trip_state=TripState(destination="Kochi"),
            conversation_context=ConversationContext(),
        )
        ctx = session.conversation_context
        # Simulate active clarification question asked in previous turn
        ctx.set_active_question(
            field="hotel",
            expected_type="clarification_answer",
            scope="clarification",
            prompt_text="I found multiple Taj hotels: Taj Malabar Resort & Spa, Taj Gateway. Which one did you mean?",
            reason="multiple_exact_matches",
        )
        ctx.last_mentioned_entities["clarification_options"] = [
            "Taj Malabar Resort & Spa",
            "Taj Gateway Hotel Marine Drive",
        ]

        # User answers with ordinal "second one"
        res = resolve_fast_path("second one", session)
        self.assertTrue(res.matched)
        self.assertEqual(res.intent, "CHANGE_HOTEL")
        self.assertEqual(res.state_updates["hotel_selection"]["name"], "Taj Gateway Hotel Marine Drive")
        self.assertTrue(res.cleared_active_question)

    def test_clarification_memory_budget_scope_answer(self):
        """Turn 1 asks budget scope -> Turn 2 'total hotel stay' resolves with stored amount."""
        session = SessionState(
            conversation_id="clar_test_2",
            trip_state=TripState(destination="Kochi", number_of_days=4, number_of_nights=3),
            conversation_context=ConversationContext(),
        )
        ctx = session.conversation_context
        ctx.set_active_question(
            field="budget",
            expected_type="clarification_answer",
            scope="clarification",
            prompt_text="Should ₹30,000 be for your nightly hotel stay, total hotel stay, or entire trip?",
            reason="ambiguous_budget_scope",
        )
        ctx.last_mentioned_entities["clarification_amount"] = 30000.0

        # User answers "total hotel stay"
        res = resolve_fast_path("total hotel stay", session)
        self.assertTrue(res.matched)
        self.assertEqual(res.intent, "UPDATE_BUDGET")
        self.assertEqual(res.state_updates["hotel_total_budget"], 30000.0)
        self.assertEqual(res.state_updates["hotel_budget"], 10000.0)  # 30000 / 3 nights
        self.assertTrue(res.cleared_active_question)

    def test_clarification_causes_zero_mutation_before_answer(self):
        """Clarification request does not alter TripState."""
        session = SessionState(
            conversation_id="clar_test_3",
            trip_state=TripState(
                destination="Kochi",
                hotel_selection=HotelDetail(property_token="h1", name="Hotel A", price_per_night=4000.0),
            ),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_visible_items("hotel", [
            {"property_token": "t1", "name": "Taj Malabar Resort & Spa", "price_per_night": 9000.0},
            {"property_token": "t2", "name": "Taj Gateway Hotel Marine Drive", "price_per_night": 7000.0},
        ])

        v_before = session.trip_state.state_version
        res = resolve_fast_path("change hotel to Taj", session)
        self.assertTrue(res.matched)
        self.assertEqual(res.intent, "CLARIFICATION")
        self.assertEqual(session.trip_state.state_version, v_before)
        self.assertEqual(session.trip_state.hotel_selection.name, "Hotel A")

    # =========================================================================
    # C. REFERENCES HARDENING
    # =========================================================================

    def test_mixed_ordinal_triggers_clarification_when_category_unspecified(self):
        """If multiple screen cards share the same index and no category is given, clarification is required."""
        session = SessionState(
            conversation_id="mixed_ord_test",
            trip_state=TripState(destination="Kochi"),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_visible_items("hotel", [
            {"property_token": "h1", "name": "Brunton Boatyard"},
            {"property_token": "h2", "name": "Grand Hyatt Kochi"},
        ])
        session.conversation_context.set_visible_items("place", [
            {"data_id": "p1", "name": "Fort Kochi"},
            {"data_id": "p2", "name": "Mattancherry Palace"},
        ])

        # "second one" without entity type is ambiguous
        res = resolve_entity_reference("second one", session.trip_state, session=session, entity_type=None)
        self.assertTrue(res.is_ambiguous)
        self.assertEqual(res.clarification.field, "mixed_ordinal")
        self.assertIn("multiple options for position #2", res.clarification.question)

    def test_mixed_ordinal_disambiguated_by_category_keyword(self):
        """'the second hotel' or 'the second place' disambiguates without confusion."""
        session = SessionState(
            conversation_id="mixed_ord_test_2",
            trip_state=TripState(destination="Kochi"),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_visible_items("hotel", [
            {"property_token": "h1", "name": "Brunton Boatyard"},
            {"property_token": "h2", "name": "Grand Hyatt Kochi"},
        ])
        session.conversation_context.set_visible_items("place", [
            {"data_id": "p1", "name": "Fort Kochi"},
            {"data_id": "p2", "name": "Mattancherry Palace"},
        ])

        res_h = resolve_entity_reference("the second hotel", session.trip_state, session=session, entity_type=None)
        self.assertFalse(res_h.is_ambiguous)
        self.assertEqual(res_h.resolved_entity.name, "Grand Hyatt Kochi")
        self.assertEqual(res_h.resolved_entity.entity_type, "hotel")

        res_p = resolve_entity_reference("the second place", session.trip_state, session=session, entity_type=None)
        self.assertFalse(res_p.is_ambiguous)
        self.assertEqual(res_p.resolved_entity.name, "Mattancherry Palace")
        self.assertEqual(res_p.resolved_entity.entity_type, "place")

    def test_no_cross_entity_ordinal_matches(self):
        """'take the first restaurant' never matches a place card."""
        session = SessionState(
            conversation_id="no_cross_test",
            trip_state=TripState(destination="Kochi"),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_visible_items("place", [
            {"data_id": "p1", "name": "Fort Kochi Beach"},
        ])
        session.conversation_context.set_visible_items("restaurant", [
            {"data_id": "r1", "name": "Malabar Cafe"},
        ])

        res = resolve_fast_path("take the first restaurant", session)
        self.assertTrue(res.matched)
        self.assertEqual(res.intent, "SELECT_ITEM")
        # Must resolve to the restaurant, NOT the place!
        self.assertIn("selected_cafes", res.state_updates or {})
        self.assertEqual(res.state_updates["selected_cafes"][0]["name"], "Malabar Cafe")

    def test_selected_entity_reference_when_disappeared_from_visible_pool(self):
        """Selected place remains resolvable even when visible_places is empty or overwritten."""
        state = TripState(
            destination="Kochi",
            selected_places=[
                Place(data_id="p_fort", name="Fort Kochi", latitude=9.9650, longitude=76.2420),
            ]
        )
        session = SessionState(
            conversation_id="hist_test",
            trip_state=state,
            conversation_context=ConversationContext(),
        )
        # Visible places is completely empty
        self.assertEqual(len(session.conversation_context.visible_places), 0)

        res = resolve_entity_reference("remove Fort Kochi", session.trip_state, session=session, entity_type=None)
        self.assertFalse(res.is_ambiguous)
        self.assertEqual(res.resolved_entity.name, "Fort Kochi")
        self.assertEqual(res.resolved_entity.precedence, ResolutionPrecedence.SELECTED_ENTITY)

    # =========================================================================
    # D. REPLACEMENT CONSTRAINTS & REJECTION
    # =========================================================================

    def test_replacement_batch_construction_and_application(self):
        """Replacement constructs atomic [REMOVE, SELECT] and applies cleanly."""
        state = TripState(
            destination="Kochi",
            selected_places=[
                Place(data_id="p1", name="Fort Kochi", latitude=9.9650, longitude=76.2420),
                Place(data_id="p2", name="Marine Drive", latitude=9.9780, longitude=76.2750),
            ]
        )
        res_entity = resolve_entity_reference("Fort Kochi", state, session=SessionState(conversation_id="rep", trip_state=state, conversation_context=ConversationContext())).resolved_entity
        self.assertIsNotNone(res_entity)

        batch = build_entity_replacement_batch(
            target_entity=res_entity,
            replacement_name_or_data={"data_id": "p3", "name": "Mattancherry Palace", "latitude": 9.9580, "longitude": 76.2590},
            source_message="replace Fort Kochi with Mattancherry Palace",
        )
        self.assertEqual(len(batch.mutations), 2)
        self.assertEqual(batch.mutations[0].target_name, "Fort Kochi")
        self.assertEqual(batch.mutations[1].target_name, "Mattancherry Palace")

        # Apply atomically
        updates = {"remove_places": ["Fort Kochi"], "selected_places": [{"data_id": "p3", "name": "Mattancherry Palace", "latitude": 9.9580, "longitude": 76.2590}]}
        cs = apply_trip_state_update(state, updates)
        place_names = [p.name for p in state.selected_places]
        self.assertIn("Mattancherry Palace", place_names)
        self.assertNotIn("Fort Kochi", place_names)
        self.assertIn("Marine Drive", place_names)

    # =========================================================================
    # E. DURATION / DATE SHORTENING CONFLICT SURFACING
    # =========================================================================

    def test_duration_shortening_preserves_all_stops_and_marks_itinerary_stale(self):
        """Shortening duration marks itinerary STALE and NEVER silently deletes user stops."""
        state = TripState(
            destination="Kochi",
            number_of_days=5,
            number_of_nights=4,
            selected_places=[
                Place(data_id="p1", name="Place 1"),
                Place(data_id="p2", name="Place 2"),
                Place(data_id="p3", name="Place 3"),
                Place(data_id="p4", name="Place 4"),
                Place(data_id="p5", name="Place 5"),
            ]
        )
        state.mark_derived_valid(DerivedResource.CURRENT_ITINERARY)

        cs = apply_trip_state_update(state, {"number_of_days": 2})
        self.assertEqual(state.number_of_days, 2)
        # Invariant: all 5 places are preserved in state!
        self.assertEqual(len(state.selected_places), 5)
        # Itinerary is marked STALE
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)

    def test_date_movement_invalidates_itinerary(self):
        """Moving trip dates invalidates itinerary."""
        state = TripState(
            destination="Kochi",
            trip_start_date=date(2026, 10, 1),
            trip_end_date=date(2026, 10, 5),
            number_of_days=5,
        )
        state.mark_derived_valid(DerivedResource.CURRENT_ITINERARY)

        cs = apply_trip_state_update(state, {
            "trip_start_date": date(2026, 10, 8),
            "trip_end_date": date(2026, 10, 12),
        })
        self.assertEqual(state.get_derived_status(DerivedResource.CURRENT_ITINERARY), DerivedStateStatus.STALE)

    # =========================================================================
    # F. STATE SAFETY & PROVENANCE INTEGRITY
    # =========================================================================

    def test_stale_route_rejected_by_replanning_controller(self):
        """Replanner strictly rejects stale route when itinerary is requested, rebuilding route first."""
        state = TripState(
            destination="Kochi",
            hotel_selection=HotelDetail(property_token="h1", name="Brunton Boatyard", latitude=9.9680, longitude=76.2440),
            selected_places=[Place(data_id="p1", name="Fort Kochi", latitude=9.9650, longitude=76.2420)],
        )
        # Mark route STALE
        state.mark_derived_stale(DerivedResource.CURRENT_ROUTE)

        action = ReplanningController.decide(state, user_intent="ITINERARY_REQUEST", user_message="update itinerary")
        self.assertEqual(action.action_type, ReplanningActionType.REBUILD_ROUTE)
        self.assertIsNotNone(action.chained_action)
        self.assertEqual(action.chained_action.action_type, ReplanningActionType.REBUILD_ITINERARY)


if __name__ == "__main__":
    unittest.main()
