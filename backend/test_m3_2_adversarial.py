"""
Project Musafir — Milestone 3, M3.2 Adversarial & Real-World Phrasing Test Suite
Verifies all real-world variations, synonyms, word order anomalies, Hinglish,
canonical city alias mappings, copy-pasted text, and multi-turn state integrity.
"""

import unittest
from fastapi.testclient import TestClient

from main import app
from app.agent.state import TripState
from app.agent.context import SessionState, ConversationContext, clear_all_sessions
from app.agent.extraction import extract_trip_slots, SlotConfidence
from app.agent.semantics import derive_trip_inferences, parse_currency_amount
from app.agent.state_update import apply_trip_state_update, get_next_active_question
from app.agent.readiness import evaluate_readiness, ReadinessAction
from app.agent.fast_path import resolve_fast_path


class TestM32Category1WordOrder(unittest.TestCase):
    """
    Category 1: Word order variation (highest-risk category for regex)
    """

    def test_01_destination_first_comma_separated(self):
        """'Kochi, one day, walking' (destination first, no 'trip'/'plan' keyword)."""
        slots = extract_trip_slots("Kochi, one day, walking")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")
        self.assertEqual(slots.confidence["destination"], SlotConfidence.HIGH)
        self.assertEqual(slots.confidence["number_of_days"], SlotConfidence.HIGH)
        self.assertEqual(slots.confidence["travel_mode"], SlotConfidence.HIGH)

    def test_02_natural_walk_around_destination_for_a_day(self):
        """'I want to walk around Kochi for a day'"""
        slots = extract_trip_slots("I want to walk around Kochi for a day")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_03_em_dash_separated(self):
        """'Walking trip — Kochi — 1 day'"""
        slots = extract_trip_slots("Walking trip — Kochi — 1 day")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_04_duration_first_mode_around_destination(self):
        """'One day walking around Kochi'"""
        slots = extract_trip_slots("One day walking around Kochi")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_05_for_a_day_leading(self):
        """'For a day, I'll walk around Kochi'"""
        slots = extract_trip_slots("For a day, I'll walk around Kochi")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_06_destination_for_a_day_on_foot(self):
        """'Kochi for a day, on foot' (synonym for walking mode)"""
        slots = extract_trip_slots("Kochi for a day, on foot")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")


class TestM32Category2Synonyms(unittest.TestCase):
    """
    Category 2: Synonyms for the same concept
    """

    def test_01_walking_synonyms(self):
        """'on foot' / 'by walking' / 'strolling around' -> travel_mode='walking'"""
        for phrase in ["trip to Jaipur on foot for 2 days", "visit Jaipur by walking for 2 days", "strolling around Jaipur for 2 days"]:
            slots = extract_trip_slots(phrase)
            self.assertEqual(slots.travel_mode, "walking", f"Failed for '{phrase}'")

    def test_02_driving_synonyms(self):
        """'by car' / 'self-drive' / 'driving myself' -> travel_mode='driving'"""
        for phrase in ["Goa for 3 days by car", "Goa for 3 days self-drive", "Goa for 3 days driving myself"]:
            slots = extract_trip_slots(phrase)
            self.assertEqual(slots.travel_mode, "driving", f"Failed for '{phrase}'")

    def test_03_overnight_and_nights_variants(self):
        """'overnight' vs '1 night' vs 'one night' -> all three parse to number_of_nights=1"""
        for phrase in ["overnight trip to Agra", "1 night trip to Agra", "one night trip to Agra"]:
            slots = extract_trip_slots(phrase)
            self.assertEqual(slots.number_of_nights, 1, f"Failed for '{phrase}'")

    def test_04_day_trip_derives_correctly(self):
        """'day trip' implies duration_days=1, nights=0, hotel_required=False"""
        slots = extract_trip_slots("day trip to Agra by car")
        self.assertEqual(slots.number_of_days, 1)
        st = TripState(destination=slots.destination, number_of_days=slots.number_of_days, travel_mode=slots.travel_mode)
        derive_trip_inferences(st)
        self.assertEqual(st.number_of_nights, 0)
        self.assertFalse(st.hotel_required)


class TestM32Category3Numbers(unittest.TestCase):
    """
    Category 3: Numbers written differently
    """

    def test_01_words_vs_digits(self):
        """'three days' (word) vs '3 days' (digit)"""
        s1 = extract_trip_slots("three days in Jaipur by car")
        s2 = extract_trip_slots("3 days in Jaipur by car")
        self.assertEqual(s1.number_of_days, 3)
        self.assertEqual(s2.number_of_days, 3)

    def test_02_hyphenation_and_phrasing_variants(self):
        """'3-day trip' vs '3 day trip' vs 'trip for 3 days'"""
        for phrase in ["3-day trip to Jaipur by car", "3 day trip to Jaipur by car", "trip to Jaipur for 3 days by car"]:
            slots = extract_trip_slots(phrase)
            self.assertEqual(slots.number_of_days, 3, f"Failed for '{phrase}'")

    def test_03_week_resolution(self):
        """'a week in Goa' resolves to 7 days"""
        slots = extract_trip_slots("a week in Goa by car")
        self.assertEqual(slots.number_of_days, 7)

    def test_04_ambiguous_duration_is_low_confidence(self):
        """'couple of days' / 'few days' must NOT silently default to 2 or 3"""
        for phrase in ["a few days in Goa", "couple of days in Goa", "some days in Goa"]:
            slots = extract_trip_slots(phrase)
            self.assertIsNone(slots.number_of_days, f"Should not set days for '{phrase}'")
            self.assertEqual(slots.confidence.get("number_of_days"), SlotConfidence.LOW)


class TestM32Category4UnusualCombinations(unittest.TestCase):
    """
    Category 4: Multiple facts in unusual combinations
    """

    def test_01_hotel_override_mid_sentence(self):
        """'Kochi trip, walking, need a hotel though, just for the day'"""
        slots = extract_trip_slots("Kochi trip, walking, need a hotel though, just for the day")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")
        self.assertTrue(slots.hotel_required)

    def test_02_comma_separated_no_verbs(self):
        """'1 day, Kochi, walking'"""
        slots = extract_trip_slots("1 day, Kochi, walking")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_03_mixed_mode_mentioned_casually(self):
        """'trip to Kochi for a day, mostly on foot but might grab a cab sometimes'"""
        slots = extract_trip_slots("trip to Kochi for a day, mostly on foot but might grab a cab sometimes")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")


class TestM32Category5CasePunctuationAndAliases(unittest.TestCase):
    """
    Category 5: Case, punctuation, informality & Canonical Indian destination aliases
    """

    def test_01_all_lowercase_no_punctuation(self):
        """'plan a 1 day walking trip to kochi'"""
        slots = extract_trip_slots("plan a 1 day walking trip to kochi")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_02_canonical_indian_city_aliases(self):
        """Historical/alternate city names normalize to canonical values"""
        test_cases = [
            ("Cochin for 1 day on foot", "Kochi"),
            ("Kochhi for 1 day on foot", "Kochi"),
            ("Bangalore for 2 days by car", "Bengaluru"),
            ("Pondicherry for 3 days by car", "Puducherry"),
            ("Bombay for 2 days by transit", "Mumbai"),
            ("Madras for 2 days by car", "Chennai"),
            ("Calcutta for 3 days by walking", "Kolkata"),
            ("Benaras for 2 days on foot", "Varanasi"),
        ]
        for phrase, expected_city in test_cases:
            slots = extract_trip_slots(phrase)
            self.assertEqual(slots.destination, expected_city, f"Failed alias for '{phrase}'")

    def test_03_conversational_filler_words(self):
        """'So basically I was thinking maybe a 1-day walking trip to Kochi if that's possible'"""
        slots = extract_trip_slots("So basically I was thinking maybe a 1-day walking trip to Kochi if that's possible")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")


class TestM32Category6StateIntegrity(unittest.TestCase):
    """
    Category 6: State integrity across turns and corrections
    """

    def setUp(self):
        clear_all_sessions()
        self.client = TestClient(app)

    def test_01_two_turn_destination_retention(self):
        """Two-turn: 'Trip to Goa' -> '3 days by car' -> destination is preserved without re-asking"""
        r1 = self.client.post("/agent/chat", json={"message": "Trip to Goa"})
        self.assertEqual(r1.status_code, 200)
        c_id = r1.json()["conversation_id"]

        r2 = self.client.post("/agent/chat", json={"conversation_id": c_id, "message": "3 days by car"})
        self.assertEqual(r2.status_code, 200)
        summary = r2.json()["state_summary"]
        self.assertEqual(summary["destination"], "Goa")
        self.assertEqual(summary["number_of_days"], 3)
        self.assertEqual(summary["travel_mode"], "driving")

    def test_02_destination_correction(self):
        """Correction: 'Actually make it Pune, not Goa' -> Goa is replaced by Pune"""
        slots = extract_trip_slots("Actually make it Pune, not Goa")
        self.assertEqual(slots.destination, "Pune")


class TestM32Category7ExplicitOverrides(unittest.TestCase):
    """
    Category 7: The explicit-override guarantees
    """

    def test_01_explicit_nights_lower_than_derived(self):
        """'5 days but only need the hotel for 3 nights'"""
        slots = extract_trip_slots("5 days in Manali by car but only need the hotel for 3 nights")
        self.assertEqual(slots.number_of_days, 5)
        self.assertEqual(slots.number_of_nights, 3)

        st = TripState(destination="Manali", number_of_days=5, number_of_nights=3, explicit_fields=["number_of_days", "number_of_nights"])
        derive_trip_inferences(st, explicit_fields={"number_of_nights"})
        self.assertEqual(st.number_of_nights, 3, "Explicit 3 nights must not be overwritten by 4")

    def test_02_hotel_afterthought_override(self):
        """'Day trip to Kochi, actually let's book a hotel too'"""
        slots = extract_trip_slots("Day trip to Kochi by car, actually let's book a hotel too")
        self.assertEqual(slots.number_of_days, 1)
        self.assertTrue(slots.hotel_required)

    def test_03_explicit_negative_hotel_override(self):
        """'2 days in Kochi, no need for a hotel' -> hotel_required=False even when days > 1"""
        slots = extract_trip_slots("2 days in Kochi by car, no need for a hotel")
        self.assertEqual(slots.number_of_days, 2)
        self.assertFalse(slots.hotel_required)

        st = TripState(destination="Kochi", number_of_days=2, hotel_required=False, travel_mode="driving")
        q = get_next_active_question(st)
        self.assertIsNone(q, "Should not ask for hotel when hotel_required=False")


class TestM32TiersRealWorldAdditions(unittest.TestCase):
    """
    Tier 1 & Tier 2 additions: Hinglish, copy-paste junk, shorthand budget, micro messages.
    """

    def test_01_hinglish_code_switching(self):
        """'Kochi jaana hai ek din ke liye, walking pe'"""
        slots = extract_trip_slots("Kochi jaana hai ek din ke liye, walking pe")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_02_copy_pasted_junk_and_parentheticals(self):
        """'Kochi trip plan - 1 day - walking (sent from someone's itinerary)'"""
        slots = extract_trip_slots("Kochi trip plan - 1 day - walking (sent from someone's itinerary)")
        self.assertEqual(slots.destination, "Kochi")
        self.assertEqual(slots.number_of_days, 1)
        self.assertEqual(slots.travel_mode, "walking")

    def test_03_shorthand_budget_parsing(self):
        """'20k', '1.5L', '20000rs', '₹20,000'"""
        self.assertEqual(parse_currency_amount("20k"), 20000.0)
        self.assertEqual(parse_currency_amount("1.5L"), 150000.0)
        self.assertEqual(parse_currency_amount("2 lakh"), 200000.0)
        self.assertEqual(parse_currency_amount("20000rs"), 20000.0)
        self.assertEqual(parse_currency_amount("₹20,000"), 20000.0)

    def test_04_micro_messages_accumulation_across_turns(self):
        """5 tiny messages: 'hi' -> 'I want to plan a trip' -> 'to Kochi' -> 'just for a day' -> 'walking'"""
        from unittest.mock import MagicMock
        from app.api.agent import get_agent_loop
        from app.agent.loop import AgentLoop, AgentResult

        mock_loop = MagicMock(spec=AgentLoop)
        def fake_run(messages, user_message, trip_state, session=None, **kwargs):
            return AgentResult(
                response=f"Noted: {user_message}",
                tool_calls=[],
                iterations=1,
                state_summary=trip_state.summary(),
                messages=[{"role": "user", "content": user_message}],
            )
        mock_loop.run.side_effect = fake_run
        app.dependency_overrides[get_agent_loop] = lambda: mock_loop

        try:
            clear_all_sessions()
            client = TestClient(app)

            # 1. hi
            r1 = client.post("/agent/chat", json={"message": "hi"})
            self.assertEqual(r1.status_code, 200)
            cid = r1.json()["conversation_id"]

            # 2. I want to plan a trip
            r2 = client.post("/agent/chat", json={"conversation_id": cid, "message": "I want to plan a trip"})
            self.assertEqual(r2.status_code, 200)

            # 3. to Kochi
            r3 = client.post("/agent/chat", json={"conversation_id": cid, "message": "to Kochi"})
            self.assertEqual(r3.status_code, 200)
            self.assertEqual(r3.json()["state_summary"].get("destination"), "Kochi")

            # 4. just for a day
            r4 = client.post("/agent/chat", json={"conversation_id": cid, "message": "just for a day"})
            self.assertEqual(r4.status_code, 200)
            self.assertEqual(r4.json()["state_summary"].get("number_of_days"), 1)
            self.assertEqual(r4.json()["state_summary"].get("number_of_nights"), 0)
            self.assertFalse(r4.json()["state_summary"].get("hotel_required"))

            # 5. walking
            r5 = client.post("/agent/chat", json={"conversation_id": cid, "message": "walking"})
            self.assertEqual(r5.status_code, 200)
            final_summary = r5.json()["state_summary"]
            self.assertEqual(final_summary.get("destination"), "Kochi")
            self.assertEqual(final_summary.get("number_of_days"), 1)
            self.assertEqual(final_summary.get("number_of_nights"), 0)
            self.assertEqual(final_summary.get("travel_mode"), "walking")
            self.assertFalse(final_summary.get("hotel_required"))
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
