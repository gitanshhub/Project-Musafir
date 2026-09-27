"""
Project Musafir — Milestone 3, M3.2 Test Suite (First-Turn Intelligence Fix)
Tests in strict order:
1. Extraction alone (schema-driven, multi-slot, confidence tracking).
2. Derivation alone (pure deterministic code, explicit outranks inferred).
3. Readiness alone (ask single highest-priority field vs proceed with zero questions).
4. Full pipeline turn-by-turn (Kochi headline acceptance test, edge cases, repeat determinism).
"""

import unittest
from fastapi.testclient import TestClient

from main import app
from app.agent.state import TripState, PlanningStage, clear_all_states
from app.agent.context import SessionState, ConversationContext, clear_all_sessions
from app.agent.extraction import extract_trip_slots, SlotConfidence
from app.agent.semantics import derive_trip_inferences
from app.agent.state_update import apply_trip_state_update, get_next_active_question
from app.agent.readiness import evaluate_readiness, ReadinessAction
from app.agent.fast_path import resolve_fast_path
from app.agent.feasibility import FeasibilityEngine, FeasibilityStatus
from app.schemas.route import OptimizedRoute, RouteSegment
from app.schemas.constraint import Constraint, ConstraintType, ConstraintSource


class TestM32ExtractionAlone(unittest.TestCase):
    """
    Phase 7, Step 1: Extraction alone.
    Feed raw sentences in, check structured output, without touching state.
    """

    def test_01_kochi_headline_sentence_extraction(self):
        """Headline acceptance test: 'Plan a 1-day walking trip to Kochi' extracts 3 slots with HIGH confidence."""
        msg = "Plan a 1-day walking trip to Kochi"
        res = extract_trip_slots(msg)

        self.assertEqual(res.destination, "Kochi")
        self.assertEqual(res.number_of_days, 1)
        self.assertEqual(res.travel_mode, "walking")
        self.assertIsNone(res.number_of_nights)
        self.assertIsNone(res.hotel_required)

        self.assertEqual(res.confidence.get("destination"), SlotConfidence.HIGH)
        self.assertEqual(res.confidence.get("number_of_days"), SlotConfidence.HIGH)
        self.assertEqual(res.confidence.get("travel_mode"), SlotConfidence.HIGH)
        self.assertIn("destination", res.explicit_fields)
        self.assertIn("number_of_days", res.explicit_fields)
        self.assertIn("travel_mode", res.explicit_fields)

    def test_02_dense_sentence_multi_slot(self):
        """Dense sentence with destination, duration, mode, diet, and interests together."""
        msg = "Plan a 3-day walking trip to Jaipur, pure vegetarian, interested in forts"
        res = extract_trip_slots(msg)

        self.assertEqual(res.destination, "Jaipur")
        self.assertEqual(res.number_of_days, 3)
        self.assertEqual(res.travel_mode, "walking")
        self.assertIn("vegetarian", res.dietary_preferences)
        self.assertIn("forts", res.interests)
        self.assertEqual(res.confidence.get("dietary_preferences"), SlotConfidence.HIGH)
        self.assertEqual(res.confidence.get("interests"), SlotConfidence.HIGH)

    def test_03_explicit_nights_extraction(self):
        """Explicit nights extraction ('5 days 6 nights')."""
        msg = "Plan a 5 days 6 nights trip to Manali by car"
        res = extract_trip_slots(msg)

        self.assertEqual(res.destination, "Manali")
        self.assertEqual(res.number_of_days, 5)
        self.assertEqual(res.number_of_nights, 6)
        self.assertEqual(res.travel_mode, "driving")
        self.assertEqual(res.confidence.get("number_of_nights"), SlotConfidence.HIGH)

    def test_04_hotel_override_extraction(self):
        """Explicit hotel request on day trip ('Day trip to Agra by car but I need a hotel')."""
        msg = "Day trip to Agra by car but I need a hotel"
        res = extract_trip_slots(msg)

        self.assertEqual(res.destination, "Agra")
        self.assertEqual(res.number_of_days, 1)
        self.assertEqual(res.travel_mode, "driving")
        self.assertTrue(res.hotel_required)
        self.assertEqual(res.confidence.get("hotel_required"), SlotConfidence.HIGH)

    def test_05_missing_duration_does_not_default(self):
        """Missing duration sentence: 'Plan a walking trip to Kochi' does NOT invent 1 day."""
        msg = "Plan a walking trip to Kochi"
        res = extract_trip_slots(msg)

        self.assertEqual(res.destination, "Kochi")
        self.assertEqual(res.travel_mode, "walking")
        self.assertIsNone(res.number_of_days)
        self.assertNotIn("number_of_days", res.explicit_fields)

    def test_06_ambiguous_duration_marked_low_confidence(self):
        """Ambiguous duration ('a few days in Goa') is not committed as fact."""
        msg = "Plan a few days in Goa"
        res = extract_trip_slots(msg)

        self.assertEqual(res.destination, "Goa")
        self.assertIsNone(res.number_of_days)
        self.assertNotIn("number_of_days", res.explicit_fields)


class TestM32DerivationAlone(unittest.TestCase):
    """
    Phase 7, Step 2: Derivation alone.
    Feed partial state directly, check nights and hotel_required derivation.
    Explicit information must NEVER be overwritten.
    """

    def test_01_one_day_derives_zero_nights_no_hotel(self):
        """duration=1 -> derived nights=0, hotel_required=False."""
        st = TripState(destination="Kochi", number_of_days=1, travel_mode="walking")
        derived = derive_trip_inferences(st)

        self.assertEqual(st.number_of_nights, 0)
        self.assertFalse(st.hotel_required)
        self.assertEqual(derived.get("number_of_nights"), 0)
        self.assertFalse(derived.get("hotel_required"))

    def test_02_multi_day_derives_nights_and_hotel_required(self):
        """duration=3 -> derived nights=2, hotel_required=True."""
        st = TripState(destination="Jaipur", number_of_days=3)
        derive_trip_inferences(st)

        self.assertEqual(st.number_of_nights, 2)
        self.assertTrue(st.hotel_required)

    def test_03_explicit_nights_never_overwritten(self):
        """Explicit nights (5 days, 6 nights) must NEVER be overwritten by 5-1=4."""
        st = TripState(
            destination="Manali",
            number_of_days=5,
            number_of_nights=6,
            explicit_fields=["number_of_days", "number_of_nights"],
        )
        derive_trip_inferences(st, explicit_fields={"number_of_nights"})

        self.assertEqual(st.number_of_nights, 6)
        self.assertTrue(st.hotel_required)

    def test_04_explicit_hotel_override_never_overwritten(self):
        """Day trip where user explicitly asked for hotel must retain hotel_required=True."""
        st = TripState(
            destination="Agra",
            number_of_days=1,
            hotel_required=True,
            explicit_fields=["number_of_days", "hotel_required"],
        )
        derive_trip_inferences(st, explicit_fields={"hotel_required"})

        self.assertTrue(st.hotel_required)


class TestM32ReadinessAlone(unittest.TestCase):
    """
    Phase 7, Step 3: Readiness alone.
    Feed state in directly, check ask vs proceed decision.
    """

    def test_01_kochi_fully_satisfied_readiness_proceeds(self):
        """Kochi state: destination, duration, mode known; 0 nights -> PROCEED, 0 questions."""
        st = TripState(
            destination="Kochi",
            number_of_days=1,
            number_of_nights=0,
            hotel_required=False,
            travel_mode="walking",
        )
        decision = evaluate_readiness(st)

        self.assertEqual(decision.action, ReadinessAction.PROCEED)
        self.assertTrue(decision.is_ready_to_proceed)
        self.assertIsNone(decision.question)

    def test_02_missing_destination_asks_destination(self):
        """Missing destination asks single targeted destination question."""
        st = TripState(number_of_days=2, travel_mode="driving")
        decision = evaluate_readiness(st)

        self.assertEqual(decision.action, ReadinessAction.ASK)
        self.assertEqual(decision.missing_field, "destination")
        self.assertIsNotNone(decision.question)
        self.assertEqual(decision.question.field, "destination")

    def test_03_missing_duration_asks_duration(self):
        """Missing duration asks single targeted duration question without defaulting."""
        st = TripState(destination="Kochi", travel_mode="walking")
        decision = evaluate_readiness(st)

        self.assertEqual(decision.action, ReadinessAction.ASK)
        self.assertEqual(decision.missing_field, "number_of_days")
        self.assertEqual(decision.question.field, "number_of_days")

    def test_04_day_trip_zero_nights_never_asks_hotel_or_dates(self):
        """Day trip with 0 nights NEVER asks for hotel budget or start date."""
        st = TripState(
            destination="Kochi",
            number_of_days=1,
            number_of_nights=0,
            hotel_required=False,
            travel_mode="walking",
        )
        q = get_next_active_question(st)
        self.assertIsNone(q)


class TestM32FullPipelineTurnByTurn(unittest.TestCase):
    """
    Phase 7, Step 4: The full pipeline, turn by turn.
    Exercises complete turns with API client and fast path.
    """

    def setUp(self):
        clear_all_sessions()
        self.client = TestClient(app)

    def test_01_kochi_headline_acceptance_test(self):
        """
        Headline acceptance test:
        'Plan a 1-day walking trip to Kochi'
        Must produce:
        - destination = 'Kochi'
        - number_of_days = 1
        - number_of_nights = 0
        - hotel_required = False
        - travel_mode = 'walking'
        - Zero unnecessary follow-up questions!
        - Repeat multiple times to guarantee determinism.
        """
        for run_idx in range(5):
            clear_all_sessions()

            resp = self.client.post("/agent/chat", json={"message": "Plan a 1-day walking trip to Kochi"})
            self.assertEqual(resp.status_code, 200, f"Run {run_idx} failed with {resp.text}")

            data = resp.json()
            summary = data.get("state_summary") or {}

            # Verify belief state correctness
            self.assertEqual(summary.get("destination"), "Kochi", f"Run {run_idx}: wrong destination")
            self.assertEqual(summary.get("number_of_days"), 1, f"Run {run_idx}: wrong days")
            self.assertEqual(summary.get("number_of_nights"), 0, f"Run {run_idx}: wrong nights")
            self.assertEqual(summary.get("travel_mode"), "walking", f"Run {run_idx}: wrong travel mode")
            self.assertFalse(summary.get("hotel_required"), f"Run {run_idx}: hotel should not be required")

            # Verify zero follow-up questions asked!
            reply = data.get("response", "")
            self.assertNotIn("Where would you like to travel?", reply)
            self.assertNotIn("How many days", reply)
            self.assertNotIn("budget would you like to keep for accommodation", reply)
            self.assertNotIn("When are you planning to start your trip", reply)
            self.assertIn("Kochi", reply)

    def test_02_dense_multi_slot_first_turn(self):
        """Dense multi-slot sentence extracts all slots into state in one turn."""
        resp = self.client.post(
            "/agent/chat",
            json={"message": "Plan a 3-day walking trip to Jaipur, pure vegetarian, interested in forts"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Jaipur")
        self.assertEqual(summary.get("number_of_days"), 3)
        self.assertEqual(summary.get("number_of_nights"), 2)
        self.assertEqual(summary.get("travel_mode"), "walking")
        self.assertTrue(summary.get("hotel_required"))

        # Jaipur + 3 days are known, so it does NOT re-ask destination or duration
        reply = data.get("response", "")
        self.assertNotIn("Where would you like to travel?", reply)
        self.assertNotIn("How many days", reply)

    def test_03_explicit_nights_retained(self):
        """5 days 6 nights retains nights=6."""
        resp = self.client.post(
            "/agent/chat",
            json={"message": "Plan a 5 days 6 nights trip to Manali by car"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Manali")
        self.assertEqual(summary.get("number_of_days"), 5)
        self.assertEqual(summary.get("number_of_nights"), 6)
        self.assertEqual(summary.get("travel_mode"), "driving")

    def test_04_missing_duration_asks_single_duration_question(self):
        """'Plan a walking trip to Kochi' does not invent duration, asks duration only."""
        resp = self.client.post(
            "/agent/chat",
            json={"message": "Plan a walking trip to Kochi"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Kochi")
        self.assertEqual(summary.get("travel_mode"), "walking")
        self.assertIsNone(summary.get("number_of_days"))

        # Asks duration, not destination
        reply = data.get("response", "")
        self.assertNotIn("Where would you like to travel?", reply)
        self.assertIn("How many days", reply)

    def test_05_hotel_override_on_day_trip(self):
        """'Day trip to Agra by car but I need a hotel' sets hotel_required=True."""
        resp = self.client.post(
            "/agent/chat",
            json={"message": "Day trip to Agra by car but I need a hotel"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary = data.get("state_summary") or {}

        self.assertEqual(summary.get("destination"), "Agra")
        self.assertEqual(summary.get("number_of_days"), 1)
        self.assertTrue(summary.get("hotel_required"))

    def test_06_two_turn_conversation_no_reasking(self):
        """Turn 1: destination given; Turn 2: duration given. Destination is not re-asked."""
        resp1 = self.client.post(
            "/agent/chat",
            json={"message": "I want to visit Goa"},
        )
        self.assertEqual(resp1.status_code, 200)
        cid = resp1.json().get("conversation_id")
        self.assertIn("How many days", resp1.json().get("response", ""))

        resp2 = self.client.post(
            "/agent/chat",
            json={"conversation_id": cid, "message": "3 days"},
        )
        self.assertEqual(resp2.status_code, 200)
        summary2 = resp2.json().get("state_summary") or {}

        self.assertEqual(summary2.get("destination"), "Goa")
        self.assertEqual(summary2.get("number_of_days"), 3)
        self.assertEqual(summary2.get("number_of_nights"), 2)

        # Destination was not re-asked
        reply2 = resp2.json().get("response", "")
        self.assertNotIn("Where would you like to travel?", reply2)

    def test_07_walking_mode_feasibility_is_advisory_not_blocking(self):
        """
        Walking mode on long route generates an informational note in state.feasibility_notes,
        not a blocking hard rejection, unless traveler set an explicit walking limit.
        """
        from app.schemas.route import RouteStop
        st = TripState(
            destination="Kochi",
            number_of_days=1,
            number_of_nights=0,
            hotel_required=False,
            travel_mode="walking",
        )
        # Create a route with 15 km of walking (exceeds default 12 km system limit)
        route = OptimizedRoute(
            ordered_stops=[
                RouteStop(id="s1", name="Stop A", latitude=9.93, longitude=76.26),
                RouteStop(id="s2", name="Stop B", latitude=9.94, longitude=76.27),
            ],
            segments=[
                RouteSegment(from_stop="Start", to_stop="Stop A", distance_meters=7500, duration_seconds=6000),
                RouteSegment(from_stop="Stop A", to_stop="Stop B", distance_meters=7500, duration_seconds=6000),
            ],
            total_distance_meters=15000,
            total_duration_seconds=12000,
            score=100.0,
        )
        res = FeasibilityEngine.evaluate(st, route=route)

        # Walking mode notes are attached to state.feasibility_notes as an informational advisory
        self.assertTrue(any("walking" in n.lower() for n in st.feasibility_notes or res.warnings))
        # The user's chosen mode remains intact (never discarded or overruled)
        self.assertEqual(st.travel_mode, "walking")
        # Feasibility engine records MODE_CONFLICT for trade-off resolution
        mode_violation = next((v for v in res.violations if v.violation_type.value == "MODE_CONFLICT"), None)
        self.assertIsNotNone(mode_violation)


if __name__ == "__main__":
    unittest.main()

