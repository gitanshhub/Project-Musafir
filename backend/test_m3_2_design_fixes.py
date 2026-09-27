"""
Project Musafir — M3.2 Design Fixes Validation Suite
Validates:
1. Accommodation Semantics (Fix #1):
   - accommodation_required (fact about trip)
   - accommodation_booked (fact about booking status)
   - hotel_search_required (derived action gate: accommodation_required and not accommodation_booked)
   - Disentangling 'no hotel needed', 'hotel already booked', and 'need a hotel'
2. Travel Mode Non-Blocking Readiness (Fix #2):
   - travel_mode is absent from required-fields gate
   - Zero questions about transport on first turn
   - Planner states explicit planning assumption when routing if mode was not specified
"""

import unittest
from app.agent.state import TripState, Place
from app.agent.extraction import extract_trip_slots, SlotConfidence
from app.agent.semantics import derive_trip_inferences
from app.agent.readiness import evaluate_readiness, ReadinessAction, REQUIRED_READINESS_FIELDS
from app.agent.planner import decide_next_planning_action, PlanningActionType, PlanningStage


class TestM32AccommodationSemantics(unittest.TestCase):
    """
    Validates Fix #1: Splitting hotel_required into three distinct, decoupled fields:
    - accommodation_required
    - accommodation_booked
    - hotel_search_required (derived)
    """

    def test_01_case1_no_hotel_needed(self):
        """Case 1: 'No hotel needed' -> accommodation_required=False, hotel_search_required=False."""
        slots = extract_trip_slots("3 days in Jaipur, no hotel needed")
        self.assertFalse(slots.accommodation_required)
        self.assertFalse(slots.accommodation_booked)
        self.assertFalse(slots.hotel_search_required)
        self.assertFalse(slots.hotel_required)

        st = TripState(
            destination=slots.destination,
            number_of_days=slots.number_of_days,
            accommodation_required=slots.accommodation_required,
            accommodation_booked=slots.accommodation_booked,
            hotel_search_required=slots.hotel_search_required,
            explicit_fields=slots.explicit_fields,
        )
        derive_trip_inferences(st, st.explicit_fields)
        self.assertFalse(st.accommodation_required)
        self.assertFalse(st.accommodation_booked)
        self.assertFalse(st.hotel_search_required)

    def test_02_case2_hotel_already_booked(self):
        """Case 2: 'Hotel already booked' -> accommodation_required=True, accommodation_booked=True, hotel_search_required=False."""
        slots = extract_trip_slots("3 days in Jaipur, hotel already booked")
        self.assertTrue(slots.accommodation_required)
        self.assertTrue(slots.accommodation_booked)
        self.assertFalse(slots.hotel_search_required)
        self.assertFalse(slots.hotel_required)

        st = TripState(
            destination=slots.destination,
            number_of_days=slots.number_of_days,
            accommodation_required=slots.accommodation_required,
            accommodation_booked=slots.accommodation_booked,
            hotel_search_required=slots.hotel_search_required,
            explicit_fields=slots.explicit_fields,
        )
        derive_trip_inferences(st, st.explicit_fields)
        # Confirms distinction: trip DOES involve accommodation, but system should NOT search
        self.assertTrue(st.accommodation_required)
        self.assertTrue(st.accommodation_booked)
        self.assertFalse(st.hotel_search_required)

    def test_03_case3_hotel_needed(self):
        """Case 3: 'I need a hotel' -> accommodation_required=True, accommodation_booked=False, hotel_search_required=True."""
        slots = extract_trip_slots("3 days in Jaipur, I need a hotel")
        self.assertTrue(slots.accommodation_required)
        self.assertFalse(slots.accommodation_booked)
        self.assertTrue(slots.hotel_search_required)
        self.assertTrue(slots.hotel_required)

        st = TripState(
            destination=slots.destination,
            number_of_days=slots.number_of_days,
            accommodation_required=slots.accommodation_required,
            accommodation_booked=slots.accommodation_booked,
            hotel_search_required=slots.hotel_search_required,
            explicit_fields=slots.explicit_fields,
        )
        derive_trip_inferences(st, st.explicit_fields)
        self.assertTrue(st.accommodation_required)
        self.assertFalse(st.accommodation_booked)
        self.assertTrue(st.hotel_search_required)

    def test_04_day_trip_positive_override_freshen_up(self):
        """Day trip (0 nights) where user explicitly wants hotel to freshen up."""
        slots = extract_trip_slots("1 day trip to Agra, but need a hotel to freshen up")
        self.assertTrue(slots.accommodation_required)
        self.assertFalse(slots.accommodation_booked)
        self.assertTrue(slots.hotel_search_required)

        st = TripState(
            destination=slots.destination,
            number_of_days=slots.number_of_days,
            accommodation_required=slots.accommodation_required,
            accommodation_booked=slots.accommodation_booked,
            explicit_fields=slots.explicit_fields,
        )
        derive_trip_inferences(st, st.explicit_fields)
        self.assertEqual(st.number_of_nights, 0)
        self.assertTrue(st.accommodation_required)
        self.assertTrue(st.hotel_search_required)

    def test_05_multi_night_staying_with_friend_negative_override(self):
        """Multi-night stay (days=4, nights=3) where user is staying with a friend."""
        slots = extract_trip_slots("4 days in Mumbai staying with a friend")
        self.assertFalse(slots.accommodation_required)
        self.assertFalse(slots.hotel_search_required)

        st = TripState(
            destination=slots.destination,
            number_of_days=slots.number_of_days,
            accommodation_required=slots.accommodation_required,
            accommodation_booked=slots.accommodation_booked,
            explicit_fields=slots.explicit_fields,
        )
        derive_trip_inferences(st, st.explicit_fields)
        self.assertEqual(st.number_of_nights, 3)
        self.assertFalse(st.accommodation_required)
        self.assertFalse(st.hotel_search_required)


class TestM32TravelModeNonBlockingReadiness(unittest.TestCase):
    """
    Validates Fix #2: Travel mode moves to optional/enrichment:
    - Never blocks readiness PROCEED decision
    - Checked and stated as a planning assumption at planner routing stage
    """

    def test_01_required_fields_excludes_travel_mode(self):
        """REQUIRED_READINESS_FIELDS strictly contains only destination and duration."""
        self.assertIn("destination", REQUIRED_READINESS_FIELDS)
        self.assertIn("duration", REQUIRED_READINESS_FIELDS)
        self.assertNotIn("travel_mode", REQUIRED_READINESS_FIELDS)

    def test_02_jaipur_without_mode_proceeds_with_zero_transport_questions(self):
        """Day trip to Jaipur without mode specified reaches PROCEED with 0 transport questions."""
        slots = extract_trip_slots("1 day in Jaipur")
        self.assertEqual(slots.destination, "Jaipur")
        self.assertEqual(slots.number_of_days, 1)
        self.assertIsNone(slots.travel_mode)

        st = TripState(
            destination=slots.destination,
            number_of_days=slots.number_of_days,
            explicit_fields=slots.explicit_fields,
        )
        derive_trip_inferences(st, st.explicit_fields)
        decision = evaluate_readiness(st)

        self.assertEqual(decision.action, ReadinessAction.PROCEED)
        self.assertTrue(decision.is_ready_to_proceed)
        self.assertIsNone(decision.question)

    def test_03_planner_states_driving_assumption_when_mode_not_explicit(self):
        """When routing is requested without explicit travel_mode, planner states assumption out loud."""
        st = TripState(
            destination="Jaipur",
            number_of_days=1,
            selected_places=[
                Place(data_id="p1", name="Hawa Mahal", latitude=26.9239, longitude=75.8267, category="attraction"),
                Place(data_id="p2", name="City Palace", latitude=26.9258, longitude=75.8237, category="attraction"),
            ],
            explicit_fields=["destination", "number_of_days"],  # Note: travel_mode is NOT explicit
        )
        action = decide_next_planning_action(st, user_intent="BUILD_ROUTE")

        self.assertEqual(action.action_type, PlanningActionType.OPTIMIZE_ROUTE)
        self.assertEqual(action.tool_name, "optimize_route")
        # Confirms travel mode is defaulted to driving
        self.assertEqual(action.tool_args.get("travel_mode"), "driving")
        # Confirms a clear planning assumption was surfaced rather than silently picking one
        self.assertIsNotNone(action.tool_args.get("planning_assumption"))
        self.assertIn("Defaulted to driving", action.tool_args.get("planning_assumption"))
        # Confirms state feasibility_notes recorded this assumption
        self.assertTrue(any("driving" in note.lower() for note in st.feasibility_notes))

    def test_04_planner_respects_explicit_travel_mode_without_assumption_warning(self):
        """When routing is requested with explicit travel_mode='walking', no default assumption is generated."""
        st = TripState(
            destination="Kochi",
            number_of_days=1,
            travel_mode="walking",
            selected_places=[
                Place(data_id="p1", name="Fort Kochi", latitude=9.9658, longitude=76.2427, category="attraction"),
                Place(data_id="p2", name="Mattancherry Palace", latitude=9.9583, longitude=76.2592, category="attraction"),
            ],
            explicit_fields=["destination", "number_of_days", "travel_mode"],
        )
        action = decide_next_planning_action(st, user_intent="BUILD_ROUTE")

        self.assertEqual(action.action_type, PlanningActionType.OPTIMIZE_ROUTE)
        self.assertEqual(action.tool_args.get("travel_mode"), "walking")
        self.assertIsNone(action.tool_args.get("planning_assumption"))


if __name__ == "__main__":
    unittest.main()
