"""
Project Musafir — Step 12.6 Unit Tests: Conversation Context, Active Question,
Semantics Engine, and Conservative Fast-Path Resolver.
"""

import unittest
from datetime import date

from app.agent.state import TripState, get_state, get_or_create_state, clear_all_states
from app.agent.context import (
    ActiveQuestion,
    VisibleItemReference,
    ConversationContext,
    SessionState,
    get_session,
    get_or_create_session,
    save_session,
    reset_session,
    delete_session,
    clear_all_sessions,
)
from app.agent.semantics import (
    calculate_nights,
    calculate_nightly_hotel_budget,
    parse_currency_amount,
    parse_duration_days,
    parse_duration_nights,
    resolve_relative_date,
    get_current_date,
)
from app.agent.fast_path import resolve_fast_path, FastPathResult


class TestStep12_6Semantics(unittest.TestCase):
    """Tests for date, night arithmetic, and budget semantics."""

    def test_01_strict_night_calculation_precedence(self):
        """Review point 5: Strict precedence for nights calculation."""
        # 1. Explicit nights has top priority
        self.assertEqual(calculate_nights(number_of_days=5, explicit_nights=5), 5)

        # 2. Check-in / check-out dates have second priority
        d1 = date(2026, 9, 18)
        d2 = date(2026, 9, 23)
        self.assertEqual(calculate_nights(number_of_days=3, start_date=d1, end_date=d2), 5)

        # 3. Only days known:
        # 1 day -> 0 nights (day trip)
        # 2 days -> 1 night
        # 5 days -> 4 nights
        self.assertEqual(calculate_nights(number_of_days=1), 0)
        self.assertEqual(calculate_nights(number_of_days=2), 1)
        self.assertEqual(calculate_nights(number_of_days=5), 4)

    def test_02_never_derive_nightly_budget_without_known_nights(self):
        """Review point 4: Never derive nightly hotel budget until nights are known, and nights > 0."""
        # No duration or dates known -> returns None
        self.assertIsNone(calculate_nightly_hotel_budget(total_hotel_budget=3500.0))

        # 1-day day trip (0 nights) -> returns None (no accommodation budget for 0 nights)
        self.assertIsNone(calculate_nightly_hotel_budget(total_hotel_budget=3500.0, number_of_days=1))

        # Nights known via number_of_days=5 -> 4 nights -> 3500 / 4 = 875.0
        self.assertEqual(
            calculate_nightly_hotel_budget(total_hotel_budget=3500.0, number_of_days=5),
            875.0,
        )

        # Nights known via explicit dates (2 nights) -> 3500 / 2 = 1750.0
        self.assertEqual(
            calculate_nightly_hotel_budget(
                total_hotel_budget=3500.0,
                start_date=date(2026, 9, 18),
                end_date=date(2026, 9, 20),
            ),
            1750.0,
        )

    def test_03_currency_amount_parsing(self):
        """Parses various human currency inputs."""
        self.assertEqual(parse_currency_amount("3500"), 3500.0)
        self.assertEqual(parse_currency_amount("total 3500"), 3500.0)
        self.assertEqual(parse_currency_amount("₹3,500"), 3500.0)
        self.assertEqual(parse_currency_amount("3.5k"), 3500.0)
        self.assertEqual(parse_currency_amount("around 5k"), 5000.0)
        self.assertEqual(parse_currency_amount("₹20,000"), 20000.0)
        self.assertEqual(parse_currency_amount("5000 per night"), 5000.0)
        self.assertIsNone(parse_currency_amount("no budget"))

    def test_04_relative_date_resolution_with_timezone(self):
        """Review point 6: Timezone-aware relative dates with defined reference."""
        # Fixed reference: Friday, 18 September 2026
        ref = date(2026, 9, 18)
        self.assertEqual(ref.weekday(), 4)  # Friday

        self.assertEqual(resolve_relative_date("today", reference_date=ref), date(2026, 9, 18))
        self.assertEqual(resolve_relative_date("tomorrow", reference_date=ref), date(2026, 9, 19))
        self.assertEqual(resolve_relative_date("day after tomorrow", reference_date=ref), date(2026, 9, 20))
        self.assertEqual(resolve_relative_date("this weekend", reference_date=ref), date(2026, 9, 19))
        self.assertEqual(resolve_relative_date("next monday", reference_date=ref), date(2026, 9, 21))
        self.assertEqual(resolve_relative_date("2026-10-15", reference_date=ref), date(2026, 10, 15))


class TestStep12_6ContextAndReferences(unittest.TestCase):
    """Tests for ConversationContext, result set tracking, and generic item references."""

    def test_05_generic_reference_resolution(self):
        """Review point 8, 9, 10: Generic references across hotels, places, and restaurants with result_set_id."""
        ctx = ConversationContext()

        # Populate hotel cards
        ctx.set_visible_items("hotel", [
            {"property_token": "tok_1", "name": "Moustache Jaipur", "price_per_night": 1200.0},
            {"property_token": "tok_2", "name": "Hotel Sarang Palace", "price_per_night": 3200.0},
            {"property_token": "tok_3", "name": "Umaid Bhawan", "price_per_night": 5500.0},
        ], result_set_id="res_hotel_01")

        # Populate restaurant cards
        ctx.set_visible_items("restaurant", [
            {"id": "rest_1", "name": "Thali And More"},
            {"id": "rest_2", "name": "LMB Sweets"},
        ], result_set_id="res_rest_01")

        # 1. Ordinals for hotels
        h2 = ctx.resolve_item_reference("the second hotel", entity_type="hotel")
        self.assertIsNotNone(h2)
        self.assertEqual(h2.name, "Hotel Sarang Palace")
        self.assertEqual(h2.index, 2)
        self.assertEqual(h2.id, "tok_2")

        h_last = ctx.resolve_item_reference("last one", entity_type="hotel")
        self.assertIsNotNone(h_last)
        self.assertEqual(h_last.name, "Umaid Bhawan")
        self.assertEqual(h_last.index, 3)

        # 2. Ordinals for restaurants
        r2 = ctx.resolve_item_reference("second restaurant", entity_type="restaurant")
        self.assertIsNotNone(r2)
        self.assertEqual(r2.name, "LMB Sweets")

        # 3. Name search
        h_match = ctx.resolve_item_reference("moustache")
        self.assertIsNotNone(h_match)
        self.assertEqual(h_match.name, "Moustache Jaipur")

    def test_06_session_state_repository_compatibility(self):
        """Review point 13: SessionState compatibility with existing TripState repository."""
        clear_all_sessions()

        session = get_or_create_session("sess_test_123")
        self.assertEqual(session.conversation_id, "sess_test_123")
        self.assertIsNotNone(session.trip_state)
        self.assertIsNotNone(session.conversation_context)

        # Mutate canonical trip state
        session.trip_state.destination = "Kashmir"
        session.trip_state.number_of_days = 5
        session.trip_state.number_of_nights = 4
        save_session(session)

        # Confirm old get_state still sees the change without breaking
        legacy_trip = get_state("sess_test_123")
        self.assertIsNotNone(legacy_trip)
        self.assertEqual(legacy_trip.destination, "Kashmir")
        self.assertEqual(legacy_trip.number_of_nights, 4)

        # Confirm conversation_context is preserved
        session.conversation_context.set_active_question(
            field="hotel_total_budget",
            expected_type="money",
            scope="accommodation",
            prompt_text="What is your total hotel budget?",
            reason="required_for_hotel_search",
        )
        save_session(session)

        reloaded = get_session("sess_test_123")
        self.assertIsNotNone(reloaded.conversation_context.active_question)
        self.assertEqual(reloaded.conversation_context.active_question.field, "hotel_total_budget")
        self.assertEqual(reloaded.conversation_context.active_question.reason, "required_for_hotel_search")

        # Test reset_session
        reset_sess = reset_session("sess_test_123")
        self.assertEqual(reset_sess.conversation_id, "sess_test_123")
        self.assertIsNone(reset_sess.trip_state.destination)
        self.assertIsNone(reset_sess.conversation_context.active_question)

        # Test delete_session
        deleted = delete_session("sess_test_123")
        self.assertTrue(deleted)
        self.assertIsNone(get_session("sess_test_123"))


class TestStep12_6FastPathResolver(unittest.TestCase):
    """Tests for conservative deterministic fast-path intent resolution."""

    def test_07_exact_user_bug_test(self):
        """Review point 14: Active question hotel_total_budget + 5 days + 'total 3500' -> 4 nights -> ₹875/night."""
        session = SessionState(
            conversation_id="bug_repro",
            trip_state=TripState(
                destination="Jaipur",
                number_of_days=5,
                number_of_nights=4,
            ),
            conversation_context=ConversationContext(),
        )

        session.conversation_context.set_active_question(
            field="hotel_total_budget",
            expected_type="money",
            scope="accommodation",
            prompt_text="What's your total hotel budget?",
        )

        res: FastPathResult = resolve_fast_path("total 3500", session)

        self.assertTrue(res.matched)
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.intent, "ANSWER_ACTIVE_QUESTION")
        self.assertEqual(res.state_updates["hotel_total_budget"], 3500.0)
        # Nightly rate: 3500 / 4 nights = 875.0
        self.assertEqual(res.state_updates["hotel_budget"], 875.0)
        self.assertTrue(res.cleared_active_question)

    def test_08_dont_touch_state_general_travel_query(self):
        """Review point 15: Pure travel question must not mutate state or trigger planning."""
        session = SessionState(
            conversation_id="info_query",
            trip_state=TripState(destination="Jaipur", number_of_days=3),
            conversation_context=ConversationContext(),
        )

        res: FastPathResult = resolve_fast_path("Is October a good time to visit Kashmir?", session)

        self.assertTrue(res.matched)
        self.assertEqual(res.intent, "GENERAL_TRAVEL_QUERY")
        self.assertEqual(res.state_updates, {})  # Zero state mutations
        self.assertFalse(res.cleared_active_question)

    def test_09_active_question_duration_answers(self):
        """Answers to duration active question."""
        session = SessionState(
            conversation_id="dur_test",
            trip_state=TripState(destination="Goa"),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_active_question(
            field="number_of_days",
            expected_type="number",
            scope="trip",
            prompt_text="How many days are you planning for?",
        )

        for input_text, expected_days, expected_nights in [
            ("5", 5, 4),
            ("5 days", 5, 4),
            ("for 7 days", 7, 6),
            ("just 3", 3, 2),
        ]:
            res = resolve_fast_path(input_text, session)
            self.assertTrue(res.matched, f"Failed on input: {input_text}")
            self.assertEqual(res.state_updates["number_of_days"], expected_days)
            self.assertEqual(res.state_updates["number_of_nights"], expected_nights)
            self.assertTrue(res.cleared_active_question)

    def test_10_active_question_travel_mode_answers(self):
        """Answers to travel mode active question."""
        session = SessionState(
            conversation_id="mode_test",
            trip_state=TripState(destination="Goa"),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_active_question(
            field="travel_mode",
            expected_type="mode",
            scope="logistics",
            prompt_text="How would you like to travel around?",
        )

        self.assertEqual(resolve_fast_path("car", session).state_updates["travel_mode"], "driving")
        self.assertEqual(resolve_fast_path("driving", session).state_updates["travel_mode"], "driving")
        self.assertEqual(resolve_fast_path("train", session).state_updates["travel_mode"], "transit")
        self.assertEqual(resolve_fast_path("walking", session).state_updates["travel_mode"], "walking")

    def test_11_active_question_confirmation_yes_no(self):
        """Review point 12: Contextual confirmation/rejection (yes/no)."""
        session = SessionState(
            conversation_id="conf_test",
            trip_state=TripState(),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_active_question(
            field="keep_budget",
            expected_type="boolean",
            scope="confirmation",
            prompt_text="Should I keep the same budget of ₹35k?",
            affirmation_field="hotel_budget",
            affirmation_value=35000.0,
        )

        res_yes = resolve_fast_path("yes", session)
        self.assertTrue(res_yes.matched)
        self.assertEqual(res_yes.intent, "CONFIRMATION_YES")
        self.assertEqual(res_yes.state_updates["hotel_budget"], 35000.0)

        res_no = resolve_fast_path("no", session)
        self.assertTrue(res_no.matched)
        self.assertEqual(res_no.intent, "CONFIRMATION_NO")
        self.assertEqual(res_no.state_updates, {})

    def test_12_standalone_card_selection(self):
        """Selecting visible hotel card without active question."""
        session = SessionState(
            conversation_id="card_sel",
            trip_state=TripState(),
            conversation_context=ConversationContext(),
        )
        session.conversation_context.set_visible_items("hotel", [
            {"property_token": "h_1", "name": "Hotel One", "price_per_night": 2000.0},
            {"property_token": "h_2", "name": "Hotel Two", "price_per_night": 3000.0},
        ])

        res = resolve_fast_path("the second hotel", session)
        self.assertTrue(res.matched)
        self.assertEqual(res.intent, "SELECT_ITEM")
        self.assertEqual(res.reference_resolution["id"], "h_2")

    def test_13_conservative_fallback_to_llm(self):
        """Review point 3: Complex or ambiguous utterances must fall back to single LLM call."""
        session = SessionState(
            conversation_id="fallback_test",
            trip_state=TripState(destination="Jaipur"),
            conversation_context=ConversationContext(),
        )

        # Complex multi-change statement
        complex_msg = "Actually let's forget Jaipur and do Kashmir instead, but keep the same budget and make it more relaxed."
        res = resolve_fast_path(complex_msg, session)
        self.assertFalse(res.matched)
        self.assertEqual(res.intent, "FALLBACK_TO_LLM")

        # Ambiguous budget without active question establishes category
        res_ambiguous = resolve_fast_path("3500", session)
        self.assertFalse(res_ambiguous.matched)
        self.assertEqual(res_ambiguous.intent, "FALLBACK_TO_LLM")

    def test_14_messy_human_inputs(self):
        """Comprehensive verification of messy, colloquial, and contrastive user messages."""
        session = SessionState(
            conversation_id="messy_test",
            trip_state=TripState(destination="Jaipur", number_of_days=5, number_of_nights=4),
            conversation_context=ConversationContext(),
        )

        # 1. 'around 5k' with active question hotel_total_budget -> resolves to ₹5000 total, ₹1250/night
        session.conversation_context.set_active_question(
            field="hotel_total_budget",
            expected_type="money",
            scope="accommodation",
            prompt_text="What's your total hotel budget?",
        )
        res_5k = resolve_fast_path("around 5k", session)
        self.assertTrue(res_5k.matched)
        self.assertEqual(res_5k.state_updates["hotel_total_budget"], 5000.0)
        self.assertEqual(res_5k.state_updates["hotel_budget"], 1250.0)
        session.conversation_context.clear_active_question()

        # 2. 'no make that 7' with active question number_of_days -> 7 days, 6 nights
        session.conversation_context.set_active_question(
            field="number_of_days",
            expected_type="number",
            scope="trip",
            prompt_text="How many days are you planning for?",
        )
        res_7d = resolve_fast_path("no make that 7", session)
        self.assertTrue(res_7d.matched)
        self.assertEqual(res_7d.state_updates["number_of_days"], 7)
        self.assertEqual(res_7d.state_updates["number_of_nights"], 6)
        session.conversation_context.clear_active_question()

        # 3. Destination pivot: "kashmir instead", "nah switch to kerala" -> MUST fallback to LLM
        for pivot_text in ["kashmir instead", "nah switch to kerala", "forget jaipur, let's do goa"]:
            res_pivot = resolve_fast_path(pivot_text, session)
            self.assertFalse(res_pivot.matched, f"Pivot '{pivot_text}' should fallback to LLM")
            self.assertEqual(res_pivot.intent, "FALLBACK_TO_LLM")

        # 4. Contrastive ordinal: "actually not the second one, the last hotel" -> MUST NOT wrongly pick hotel #2!
        session.conversation_context.set_visible_items("hotel", [
            {"property_token": "h_1", "name": "Hotel One", "price_per_night": 2000.0},
            {"property_token": "h_2", "name": "Hotel Two", "price_per_night": 3000.0},
            {"property_token": "h_3", "name": "Hotel Three", "price_per_night": 4000.0},
        ])
        res_contrast = resolve_fast_path("actually not the second one, the last hotel", session)
        # Because of the negation "not", conservative resolver rejects deterministic guess and delegates to LLM
        self.assertFalse(res_contrast.matched)
        self.assertEqual(res_contrast.intent, "FALLBACK_TO_LLM")

        # 5. Non-contrastive direct ordinals STILL work cleanly:
        res_direct = resolve_fast_path("the second hotel", session)
        self.assertTrue(res_direct.matched)
        self.assertEqual(res_direct.reference_resolution["index"], 2)

        # 6. Nuanced preferences & open-ended reset statements -> MUST fallback to LLM
        for nuanced_text in [
            "somewhere near the taj",
            "best places in india for a 4 day trip under 30k",
            "don't want too much driving",
            "I changed my mind",
            "forget the whole thing",
        ]:
            res_nuance = resolve_fast_path(nuanced_text, session)
            self.assertFalse(res_nuance.matched, f"Nuanced '{nuanced_text}' should fallback to LLM")
            self.assertEqual(res_nuance.intent, "FALLBACK_TO_LLM")


if __name__ == "__main__":
    unittest.main()

